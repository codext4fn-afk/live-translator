"""WASAPI loopback capture: records what your speakers are playing."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass

import numpy as np
import pyaudiowpatch as pyaudio
import soxr

log = logging.getLogger(__name__)

TARGET_RATE = 16_000
# 20 ms at 16 kHz
FRAME_SAMPLES = 320


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    channels: int
    rate: int
    is_default: bool

    def __str__(self) -> str:
        marker = " (default)" if self.is_default else ""
        return f"[{self.index}] {self.name}{marker} - {self.channels}ch @ {self.rate} Hz"


class AudioError(RuntimeError):
    """Raised when no usable loopback device can be opened."""


def _default_output_name(pa: pyaudio.PyAudio) -> str:
    try:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError as exc:
        raise AudioError(
            "WASAPI host API unavailable. This tool requires Windows Vista or newer."
        ) from exc
    try:
        info = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
    except (OSError, KeyError):
        return ""
    return str(info.get("name", ""))


def list_devices() -> list[DeviceInfo]:
    """Enumerate loopback-capable devices, default first."""
    pa = pyaudio.PyAudio()
    try:
        default_name = _default_output_name(pa)
        found: list[DeviceInfo] = []
        for info in pa.get_loopback_device_info_generator():
            name = str(info["name"])
            found.append(
                DeviceInfo(
                    index=int(info["index"]),
                    name=name,
                    channels=int(info["maxInputChannels"]),
                    rate=int(info["defaultSampleRate"]),
                    # Loopback names are the output name plus a suffix
                    is_default=bool(default_name) and default_name in name,
                )
            )
    finally:
        pa.terminate()

    found.sort(key=lambda d: (not d.is_default, d.index))
    return found


def pick_device(name_filter: str = "") -> DeviceInfo:
    """Choose a loopback device: substring match if given, else the default output."""
    devices = list_devices()
    if not devices:
        raise AudioError(
            "No WASAPI loopback devices found. Check that an audio output device is "
            "enabled in Windows Sound settings."
        )

    if name_filter:
        needle = name_filter.casefold()
        matches = [d for d in devices if needle in d.name.casefold()]
        if not matches:
            available = "\n  ".join(str(d) for d in devices)
            raise AudioError(
                f"No loopback device matches {name_filter!r}. Available:\n  {available}"
            )
        return matches[0]

    for device in devices:
        if device.is_default:
            return device
    return devices[0]


class LoopbackCapture:
    """Background capture of system audio as 20 ms frames of 16 kHz mono float32."""

    def __init__(self, device: DeviceInfo, max_queued_frames: int = 400) -> None:
        self.device = device
        self.frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_queued_frames)
        self.dropped_frames = 0

        self._pa: pyaudio.PyAudio | None = None
        self._stream = None
        self._resampler: soxr.ResampleStream | None = None
        self._tail = np.zeros(0, dtype=np.float32)
        self._lock = threading.Lock()
        self._paused = False
        # incoming audio is dropped until this deadline
        self._suppress_until = 0.0


    def start(self) -> None:
        self._pa = pyaudio.PyAudio()
        channels = max(1, self.device.channels)

        if self.device.rate != TARGET_RATE:
            self._resampler = soxr.ResampleStream(
                self.device.rate, TARGET_RATE, 1, dtype="float32", quality="HQ"
            )

        # ~20 ms of input per callback, low latency without waking constantly
        frames_per_buffer = max(160, int(self.device.rate * 0.02))

        try:
            self._stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=self.device.rate,
                input=True,
                input_device_index=self.device.index,
                frames_per_buffer=frames_per_buffer,
                stream_callback=self._on_audio,
            )
        except OSError as exc:
            self._pa.terminate()
            self._pa = None
            raise AudioError(
                f"Could not open loopback device {self.device.name!r}: {exc}"
            ) from exc

        log.info("capturing from %s", self.device)

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except OSError:
                pass
            self._stream = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None

    def __enter__(self) -> LoopbackCapture:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


    def set_paused(self, paused: bool) -> None:
        """Stop publishing frames without tearing down the stream."""
        with self._lock:
            self._paused = paused
            if paused:
                self._tail = np.zeros(0, dtype=np.float32)
        if paused:
            self._drain()

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def suppress_for(self, milliseconds: int) -> None:
        """Ignore captured audio for a moment."""
        deadline = time.monotonic() + milliseconds / 1000.0
        with self._lock:
            self._suppress_until = max(self._suppress_until, deadline)

    def _drain(self) -> None:
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                return


    def _on_audio(self, in_data, _frame_count, _time_info, _status):  # noqa: ANN001
        """PortAudio callback. Must return promptly - no blocking work here."""
        with self._lock:
            if self._paused:
                return None, pyaudio.paContinue
            if time.monotonic() < self._suppress_until:
                # Keep the resampler fed so its phase stays continuous
                self._tail = np.zeros(0, dtype=np.float32)
                return None, pyaudio.paContinue

        try:
            block = np.frombuffer(in_data, dtype=np.float32)
            channels = max(1, self.device.channels)
            if channels > 1:
                usable = (block.size // channels) * channels
                block = block[:usable].reshape(-1, channels).mean(axis=1)

            if self._resampler is not None:
                block = self._resampler.resample_chunk(block)

            if block.size:
                self._emit(np.asarray(block, dtype=np.float32))
        except Exception:  # pragma: no cover - never let an exception kill the stream
            log.exception("audio callback failed")

        return None, pyaudio.paContinue

    def _emit(self, samples: np.ndarray) -> None:
        """Slice a continuous stream into exact 20 ms frames."""
        buf = np.concatenate((self._tail, samples)) if self._tail.size else samples

        count = buf.size // FRAME_SAMPLES
        for i in range(count):
            frame = buf[i * FRAME_SAMPLES : (i + 1) * FRAME_SAMPLES]
            try:
                self.frames.put_nowait(frame)
            except queue.Full:
                try:
                    self.frames.get_nowait()
                    self.frames.put_nowait(frame)
                except (queue.Empty, queue.Full):
                    pass
                self.dropped_frames += 1

        self._tail = buf[count * FRAME_SAMPLES :].copy()
