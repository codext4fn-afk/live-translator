"""Interface sounds, synthesised at first run and played through winsound."""

from __future__ import annotations

import logging
import sys
import time
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

RATE = 44_100

# name -> (build function arguments); durations in seconds.
_SPECS: dict[str, dict] = {
    # Hover: barely there. It fires constantly as the pointer crosses the window, so
    # it has to sit at the edge of perception or it becomes maddening.
    "hover": {"freqs": [(2100.0, 1.0)], "duration": 0.016, "gain": 0.05, "decay": 40.0},
    # Chrome buttons: a dry, high tap.
    "tap": {"freqs": [(1180.0, 1.0)], "duration": 0.030, "gain": 0.16, "decay": 26.0},
    # Reply / row click: slightly rounder.
    "click": {"freqs": [(880.0, 1.0), (1760.0, 0.25)], "duration": 0.040,
              "gain": 0.18, "decay": 22.0},
    # Copied to clipboard: a rising two-note confirmation.
    "copy": {"sequence": [(920.0, 0.045), (1380.0, 0.075)], "gain": 0.17, "decay": 16.0},
    # Sending something to translate: a short upward sweep.
    "send": {"sweep": (620.0, 1240.0), "duration": 0.090, "gain": 0.16, "decay": 11.0},
    # Picking the window up and putting it down.
    "grab": {"freqs": [(300.0, 1.0), (150.0, 0.5)], "duration": 0.045,
             "gain": 0.13, "decay": 24.0},
    "drop": {"freqs": [(220.0, 1.0), (110.0, 0.5)], "duration": 0.055,
             "gain": 0.13, "decay": 20.0},
    # A new translation arrived.
    "card": {"sequence": [(1046.5, 0.050), (1568.0, 0.070)], "gain": 0.13, "decay": 14.0},
    # Something went wrong.
    "error": {"sequence": [(440.0, 0.070), (330.0, 0.090)], "gain": 0.15, "decay": 12.0},
}


def _envelope(n: int, decay: float) -> np.ndarray:
    """Fast raised-cosine attack into an exponential tail."""
    t = np.linspace(0.0, 1.0, n, endpoint=False)
    attack_samples = max(1, int(0.004 * RATE))
    envelope = np.exp(-decay * t)
    ramp = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, min(attack_samples, n))))
    envelope[: ramp.size] *= ramp
    # Always land on silence so consecutive plays do not pop.
    release = max(1, int(0.006 * RATE))
    if n > release:
        envelope[-release:] *= np.linspace(1.0, 0.0, release)
    return envelope


def _tone(spec: dict) -> np.ndarray:
    gain = spec.get("gain", 0.15)
    decay = spec.get("decay", 18.0)

    if "sequence" in spec:
        parts = []
        for freq, duration in spec["sequence"]:
            n = int(duration * RATE)
            t = np.arange(n) / RATE
            parts.append(np.sin(2 * np.pi * freq * t) * _envelope(n, decay))
        wave_data = np.concatenate(parts)
    elif "sweep" in spec:
        start, end = spec["sweep"]
        n = int(spec["duration"] * RATE)
        t = np.arange(n) / RATE
        # Linear chirp: integrate the instantaneous frequency for the phase.
        freq = np.linspace(start, end, n)
        phase = 2 * np.pi * np.cumsum(freq) / RATE
        wave_data = np.sin(phase) * _envelope(n, decay)
    else:
        n = int(spec["duration"] * RATE)
        t = np.arange(n) / RATE
        wave_data = np.zeros(n)
        for freq, weight in spec["freqs"]:
            wave_data += weight * np.sin(2 * np.pi * freq * t)
        wave_data *= _envelope(n, decay)
        peak = np.max(np.abs(wave_data)) or 1.0
        wave_data /= peak

    return np.clip(wave_data * gain, -1.0, 1.0)


def _write_wav(path: Path, samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(pcm.tobytes())


class SoundBoard:
    """Synthesises the sound set once, then plays cues by name."""

    def __init__(self, directory: Path, *, enabled: bool = True,
                 volume: float = 1.0) -> None:
        self.enabled = enabled and sys.platform == "win32"
        self.volume = max(0.0, min(1.0, volume))
        self._dir = directory
        self._paths: dict[str, Path] = {}
        self._duration_ms: dict[str, int] = {}
        self._on_play: Callable[[int], None] | None = None
        self._last_hover = 0.0
        self._quiet_until = 0.0

        if self.enabled:
            try:
                self._build()
            except Exception:
                log.exception("could not prepare interface sounds; disabling them")
                self.enabled = False

    def set_playback_listener(self, fn: Callable[[int], None]) -> None:
        """Called with a duration in ms just before a sound plays."""
        self._on_play = fn

    def _build(self) -> None:
        for name, spec in _SPECS.items():
            samples = _tone(spec) * self.volume
            path = self._dir / f"{name}.wav"
            # Regenerate only when missing or stale, so startup stays fast.
            if not path.is_file() or path.stat().st_size < 64:
                _write_wav(path, samples)
            self._paths[name] = path
            self._duration_ms[name] = int(1000 * samples.size / RATE)

    def play(self, name: str) -> None:
        if not self.enabled:
            return
        path = self._paths.get(name)
        if path is None or not path.is_file():
            return

        # winsound plays one sound at a time, so a stream of hover events would cut
        # off the click or copy cue the user actually cares about.
        now = time.monotonic()
        if name == "hover":
            if now < self._quiet_until or now - self._last_hover < 0.09:
                return
            self._last_hover = now
        else:
            self._quiet_until = now + self._duration_ms.get(name, 80) / 1000.0

        duration = self._duration_ms.get(name, 80)
        if self._on_play is not None:
            try:
                # Pad generously: the speaker keeps ringing after the file ends.
                self._on_play(duration + 180)
            except Exception:
                log.debug("playback listener failed", exc_info=True)

        try:
            import winsound

            winsound.PlaySound(
                str(path),
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
        except Exception:
            log.debug("sound playback failed", exc_info=True)


def silent() -> SoundBoard:
    """A board that does nothing, for tests and --no-sound."""
    board = SoundBoard.__new__(SoundBoard)
    board.enabled = False
    board.volume = 0.0
    board._paths = {}
    board._duration_ms = {}
    board._on_play = None
    board._last_hover = 0.0
    board._quiet_until = 0.0
    return board
