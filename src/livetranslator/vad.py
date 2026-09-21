"""Turn a continuous audio stream into discrete utterances."""

from __future__ import annotations

import collections
import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .audio import FRAME_SAMPLES, TARGET_RATE

log = logging.getLogger(__name__)

FRAME_MS = 1000 * FRAME_SAMPLES // TARGET_RATE  # 20
_SILENCE_FLOOR_DBFS = -120.0


@dataclass
class Utterance:
    """A contiguous stretch of detected speech."""

    samples: np.ndarray
    started_at: float
    peak_dbfs: float
    ended_by_timeout: bool = False
    seq: int = field(default=0)

    @property
    def duration_s(self) -> float:
        return len(self.samples) / TARGET_RATE


def dbfs(frame: np.ndarray) -> float:
    """RMS level of a float32 frame in dBFS (0 dB = full scale)."""
    if frame.size == 0:
        return _SILENCE_FLOOR_DBFS
    rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
    if rms <= 1e-9:
        return _SILENCE_FLOOR_DBFS
    return 20.0 * float(np.log10(rms))


class VadBackend(Protocol):
    def is_speech(self, frame: np.ndarray) -> bool: ...

    @property
    def name(self) -> str: ...


class WebRtcVad:
    """WebRTC VAD. Wants 16-bit mono PCM in 10/20/30 ms frames."""

    def __init__(self, aggressiveness: int) -> None:
        import webrtcvad

        self._vad = webrtcvad.Vad(aggressiveness)
        self._aggressiveness = aggressiveness

    @property
    def name(self) -> str:
        return f"webrtcvad(mode={self._aggressiveness})"

    def is_speech(self, frame: np.ndarray) -> bool:
        pcm = np.clip(frame * 32767.0, -32768, 32767).astype(np.int16)
        try:
            return self._vad.is_speech(pcm.tobytes(), TARGET_RATE)
        except Exception:
            return False


class EnergyVad:
    """Fallback when webrtcvad is missing: adaptive level tracking."""

    def __init__(self, margin_db: float = 9.0) -> None:
        self._floor: float | None = None
        self._margin = margin_db

    @property
    def name(self) -> str:
        return "energy(fallback)"

    def is_speech(self, frame: np.ndarray) -> bool:
        level = dbfs(frame)
        if self._floor is None:
            self._floor = level
            return False
        speech = level > self._floor + self._margin
        # Track quiet frames quickly, loud frames barely.
        alpha = 0.02 if speech else 0.15
        self._floor += alpha * (level - self._floor)
        return speech


def make_vad(aggressiveness: int) -> VadBackend:
    try:
        return WebRtcVad(aggressiveness)
    except Exception as exc:
        log.warning("webrtcvad unavailable (%s) - falling back to energy VAD", exc)
        return EnergyVad()


class NoiseGate:
    """Adaptive level gate: speech must stand above the ambient floor."""

    _RISE = 0.02   # loud ambience raises the floor slowly
    _FALL = 0.25   # quiet reopens the gate quickly
    _MIN_FLOOR = -80.0
    _MAX_FLOOR = -25.0  # never demand more than this of speech

    def __init__(self, *, margin_db: float = 6.0, absolute_floor_dbfs: float = -55.0) -> None:
        self.margin_db = margin_db
        self.absolute_floor = absolute_floor_dbfs
        self._floor: float | None = None

    @property
    def threshold(self) -> float:
        if self._floor is None:
            return self.absolute_floor
        return max(self.absolute_floor, self._floor + self.margin_db)

    @property
    def floor(self) -> float:
        return self._floor if self._floor is not None else self._MIN_FLOOR

    def observe_ambient(self, level: float) -> None:
        """Feed a frame the VAD judged not to be speech."""
        if self._floor is None:
            self._floor = level
            return
        alpha = self._RISE if level > self._floor else self._FALL
        self._floor += alpha * (level - self._floor)
        self._floor = min(self._MAX_FLOOR, max(self._MIN_FLOOR, self._floor))

    def allows(self, level: float) -> bool:
        return level >= self.threshold


class Segmenter:
    """Feed it 20 ms frames; it hands back complete utterances."""

    # Fraction of the onset window that must be voiced to start capturing.
    _ONSET_RATIO = 0.6
    _ONSET_WINDOW_FRAMES = 5  # 100 ms

    def __init__(
        self,
        *,
        vad_aggressiveness: int = 2,
        silence_ms: int = 700,
        min_speech_ms: int = 500,
        max_utterance_ms: int = 9_000,
        pre_roll_ms: int = 300,
        noise_gate_dbfs: float = -55.0,
        noise_margin_db: float = 6.0,
        chunk_after_ms: int = 2_500,
        chunk_silence_ms: int = 150,
    ) -> None:
        self._vad = make_vad(vad_aggressiveness)
        self._gate = NoiseGate(
            margin_db=noise_margin_db, absolute_floor_dbfs=noise_gate_dbfs
        )

        self._silence_frames_needed = max(1, silence_ms // FRAME_MS)
        # Adaptive endpointing.
        self._chunk_after_frames = max(1, chunk_after_ms // FRAME_MS)
        self._chunk_silence_frames = max(1, chunk_silence_ms // FRAME_MS)
        self._min_speech_frames = max(1, min_speech_ms // FRAME_MS)
        self._max_frames = max(self._min_speech_frames, max_utterance_ms // FRAME_MS)
        pre_roll_frames = max(0, pre_roll_ms // FRAME_MS)

        self._pre_roll: collections.deque[np.ndarray] = collections.deque(
            maxlen=pre_roll_frames
        )
        self._onset: collections.deque[bool] = collections.deque(
            maxlen=self._ONSET_WINDOW_FRAMES
        )

        self._active = False
        self._collected: list[np.ndarray] = []
        self._silence_run = 0
        self._voiced_frames = 0
        self._started_at = 0.0
        self._seq = 0

        # Diagnostics surfaced in the UI status bar.
        self.frames_seen = 0
        self.frames_gated = 0

    @property
    def backend_name(self) -> str:
        return self._vad.name

    @property
    def gate_floor_dbfs(self) -> float:
        """Current learned ambient floor - shown in the status bar for tuning."""
        return self._gate.floor

    @property
    def gate_threshold_dbfs(self) -> float:
        return self._gate.threshold

    @property
    def active(self) -> bool:
        """True while speech is being collected - drives the UI listening indicator."""
        return self._active

    def reset(self) -> None:
        self._active = False
        # Rebind rather than clear(): _finish holds a reference to this list.
        self._collected = []
        self._silence_run = 0
        self._voiced_frames = 0
        self._pre_roll.clear()
        self._onset.clear()

    def push(self, frame: np.ndarray) -> Utterance | None:
        """Consume one 20 ms frame. Returns an utterance when one just completed."""
        self.frames_seen += 1

        level = dbfs(frame)
        # Ask the VAD first, then let the gate veto.
        voiced = self._vad.is_speech(frame)
        if not voiced:
            self._gate.observe_ambient(level)
        elif not self._gate.allows(level):
            voiced = False
            self.frames_gated += 1

        if not self._active:
            self._pre_roll.append(frame)
            self._onset.append(voiced)
            if len(self._onset) == self._onset.maxlen:
                ratio = sum(self._onset) / len(self._onset)
                if ratio >= self._ONSET_RATIO:
                    self._begin()
            return None

        self._collected.append(frame)
        if voiced:
            self._voiced_frames += 1
            self._silence_run = 0
        else:
            self._silence_run += 1

        if self._silence_run >= self._required_silence():
            return self._finish(timed_out=False)

        if len(self._collected) >= self._max_frames:
            return self._finish(timed_out=True)

        return None

    def _required_silence(self) -> int:
        """How much trailing silence ends the current utterance."""
        if len(self._collected) >= self._chunk_after_frames:
            return self._chunk_silence_frames
        return self._silence_frames_needed

    def flush(self) -> Utterance | None:
        """End any in-progress utterance, e.g. on pause or shutdown."""
        if not self._active:
            return None
        return self._finish(timed_out=True)


    def _begin(self) -> None:
        self._active = True
        # The pre-roll already contains the onset frames.
        self._collected = list(self._pre_roll)
        # Carry over only the frames the VAD actually called speech.
        self._voiced_frames = sum(self._onset)
        self._pre_roll.clear()
        self._onset.clear()
        self._silence_run = 0
        self._started_at = time.monotonic()

    def _finish(self, *, timed_out: bool) -> Utterance | None:
        # Snapshot everything before reset() touches the instance state.
        frames = list(self._collected)
        voiced = self._voiced_frames
        started = self._started_at
        # Trim the tail that actually triggered the cut.
        silence_tail = self._required_silence()
        self.reset()

        if not frames or voiced < self._min_speech_frames:
            return None

        # Drop the trailing silence that triggered the cut.
        if not timed_out and silence_tail < len(frames):
            keep = len(frames) - max(0, silence_tail - 2)
            frames = frames[:keep]

        samples = np.concatenate(frames).astype(np.float32, copy=False)
        self._seq += 1
        return Utterance(
            samples=samples,
            started_at=started,
            peak_dbfs=dbfs(samples),
            ended_by_timeout=timed_out,
            seq=self._seq,
        )
