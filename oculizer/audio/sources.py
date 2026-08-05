"""Pluggable audio sources that deliver PortAudio-shaped chunks."""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path
from typing import Callable, Protocol

import numpy as np


AudioCallback = Callable[[np.ndarray, int, object, object], None]


class AudioSource(Protocol):
    sample_rate: int
    channels: int

    def start(self) -> None: ...
    def request_stop(self) -> None: ...
    def stop(self) -> None: ...
    def join(self, timeout: float | None = None) -> None: ...
    def is_alive(self) -> bool: ...


class SoundDeviceAudioSource:
    """Adapt a live PortAudio input stream to the shared source lifecycle."""

    def __init__(self, *, device, channels, sample_rate, block_size, callback):
        self.device = device
        self.channels = int(channels)
        self.sample_rate = int(sample_rate)
        self.block_size = int(block_size)
        self.callback = callback
        self.stream = None

    def start(self) -> None:
        import sounddevice as sd

        self.stream = sd.InputStream(
            device=self.device,
            channels=self.channels,
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            callback=self.callback,
        )
        self.stream.start()

    def stop(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def request_stop(self) -> None:
        """Let the owning runtime thread close the native PortAudio stream."""
        return None

    def join(self, timeout: float | None = None) -> None:
        return None

    def is_alive(self) -> bool:
        return bool(self.stream is not None and self.stream.active)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.stop()


class WavFileAudioSource(threading.Thread):
    """Stream a PCM WAV file repeatedly at its recorded real-time pace."""

    def __init__(
        self,
        path: str | Path,
        callback: AudioCallback,
        block_size: int,
        *,
        loop: bool = True,
        on_loop: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        super().__init__(name="oculizer-wav-source", daemon=True)
        self.path = Path(path).expanduser().resolve()
        self.callback = callback
        self.block_size = int(block_size)
        self.loop = loop
        self.on_loop = on_loop
        self.clock = clock
        self._stop_event = threading.Event()
        self.error: Exception | None = None

        if self.block_size < 1:
            raise ValueError("WAV block size must be at least one frame")
        if not self.path.is_file():
            raise ValueError(f"WAV audio file does not exist: {self.path}")
        try:
            with wave.open(str(self.path), "rb") as wav:
                self.channels = wav.getnchannels()
                self.sample_width = wav.getsampwidth()
                self.sample_rate = wav.getframerate()
                self.frame_count = wav.getnframes()
                compression = wav.getcomptype()
        except (OSError, EOFError, wave.Error) as exc:
            raise ValueError(f"Invalid WAV audio file '{self.path}': {exc}") from exc
        if compression != "NONE":
            raise ValueError(f"WAV audio file must contain uncompressed PCM data: {self.path}")
        if self.channels < 1 or self.sample_rate < 1 or self.frame_count < 1:
            raise ValueError(f"WAV audio file contains no playable audio frames: {self.path}")
        if self.sample_width not in (1, 2, 3, 4):
            raise ValueError(f"Unsupported WAV sample width: {self.sample_width} bytes")

    def _decode(self, raw: bytes) -> np.ndarray:
        if self.sample_width == 1:
            samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif self.sample_width == 2:
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif self.sample_width == 4:
            samples = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
        else:
            packed = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
            values = (
                packed[:, 0].astype(np.int32)
                | (packed[:, 1].astype(np.int32) << 8)
                | (packed[:, 2].astype(np.int32) << 16)
            )
            values = np.where(values & 0x800000, values - 0x1000000, values)
            samples = values.astype(np.float32) / 8388608.0
        frames = samples.reshape(-1, self.channels)
        # File input has one semantic stream. Average every file channel once,
        # before the shared callback performs resampling and analysis.
        return np.mean(frames, axis=1, dtype=np.float32).reshape(-1, 1)

    def run(self) -> None:
        try:
            with wave.open(str(self.path), "rb") as wav:
                deadline = self.clock()
                while not self._stop_event.is_set():
                    raw = wav.readframes(self.block_size)
                    if not raw:
                        if not self.loop:
                            break
                        wav.rewind()
                        if self.on_loop is not None:
                            self.on_loop()
                        deadline = self.clock()
                        continue
                    chunk = self._decode(raw)
                    self.callback(chunk, len(chunk), None, None)
                    deadline += len(chunk) / self.sample_rate
                    self._stop_event.wait(max(0.0, deadline - self.clock()))
        except Exception as exc:  # surfaced by the owning runtime
            self.error = exc

    def stop(self) -> None:
        self._stop_event.set()

    def request_stop(self) -> None:
        self._stop_event.set()
