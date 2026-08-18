"""Pluggable audio sources that deliver PortAudio-shaped chunks."""

from __future__ import annotations

import logging
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Protocol

import numpy as np


logger = logging.getLogger(__name__)
AudioCallback = Callable[[np.ndarray, int, object, object], None]


def list_audio_input_devices():
    """Return a compact current PortAudio input inventory on explicit request."""
    import sounddevice as sd

    devices = sd.query_devices()
    try:
        default_index = int(sd.query_devices(kind="input")["index"])
    except (KeyError, TypeError, ValueError, sd.PortAudioError):
        try:
            default_index = int(sd.default.device[0])
        except (AttributeError, IndexError, TypeError, ValueError):
            default_index = None
    result = []
    for index, device in enumerate(devices):
        channels = int(device.get("max_input_channels", 0))
        if channels <= 0:
            continue
        result.append({
            "index": index,
            "name": str(device.get("name", f"Input {index}")),
            "channels": channels,
            "default": index == default_index,
        })
    return result


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

    def __init__(
        self,
        *,
        device,
        channels,
        sample_rate,
        block_size,
        callback,
        interrupt_timeout: float = 0.5,
        shutdown_timeout: float = 2.0,
    ):
        self.device = device
        self.channels = int(channels)
        self.sample_rate = int(sample_rate)
        self.block_size = int(block_size)
        self.callback = callback
        self.interrupt_timeout = float(interrupt_timeout)
        self.shutdown_timeout = float(shutdown_timeout)
        if self.interrupt_timeout <= 0:
            raise ValueError("interrupt_timeout must be greater than zero")
        if self.shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be greater than zero")
        self.stream = None
        self._state_lock = threading.Lock()
        self._interrupt_thread = None
        self._interrupt_done = threading.Event()
        self._interrupt_requested = False
        self._native_stage = None

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

    def _timed_native_call(self, stage: str, callback) -> None:
        started = time.monotonic()
        with self._state_lock:
            self._native_stage = stage
        logger.info("PortAudio shutdown: %s started", stage)
        try:
            callback()
        except Exception:
            logger.warning("PortAudio shutdown: %s raised", stage, exc_info=True)
        finally:
            elapsed = time.monotonic() - started
            logger.info("PortAudio shutdown: %s finished in %.3fs", stage, elapsed)
            with self._state_lock:
                if self._native_stage == stage:
                    self._native_stage = None

    def _interrupt_stream(self, stream) -> None:
        try:
            self._timed_native_call("stream.abort()", stream.abort)
        finally:
            self._interrupt_done.set()

    def request_stop(self) -> None:
        """Interrupt capture promptly without closing the stream from the caller."""
        with self._state_lock:
            stream = self.stream
            if stream is None or self._interrupt_requested:
                return
            self._interrupt_requested = True
            self._interrupt_done.clear()
            thread = threading.Thread(
                target=self._interrupt_stream,
                args=(stream,),
                name="oculizer-portaudio-abort",
                daemon=True,
            )
            self._interrupt_thread = thread
        logger.info("PortAudio shutdown interrupt requested")
        thread.start()

    def _close_stream(self, stream, *, interrupted: bool) -> None:
        if not interrupted:
            self._timed_native_call("stream.stop()", stream.stop)
        else:
            logger.info("PortAudio shutdown: stream.stop() skipped after interrupt")
        self._timed_native_call("stream.close()", stream.close)

    def stop(self) -> None:
        with self._state_lock:
            stream = self.stream
            interrupt_thread = self._interrupt_thread
            interrupted = self._interrupt_requested
        if stream is None:
            return

        if interrupt_thread is not None and interrupt_thread.is_alive():
            logger.info(
                "PortAudio shutdown: waiting up to %.3fs for stream.abort()",
                self.interrupt_timeout,
            )
            if not self._interrupt_done.wait(self.interrupt_timeout):
                with self._state_lock:
                    stage = self._native_stage or "stream.abort()"
                    if self.stream is stream:
                        self.stream = None
                logger.error(
                    "PortAudio shutdown: %s did not finish within %.3fs; "
                    "leaving the native call on a daemon thread so application shutdown can continue",
                    stage,
                    self.interrupt_timeout,
                )
                return

        close_thread = threading.Thread(
            target=self._close_stream,
            args=(stream,),
            kwargs={"interrupted": interrupted},
            name="oculizer-portaudio-close",
            daemon=True,
        )
        close_thread.start()
        close_thread.join(self.shutdown_timeout)
        if close_thread.is_alive():
            with self._state_lock:
                stage = self._native_stage or "native shutdown"
                if self.stream is stream:
                    self.stream = None
            logger.error(
                "PortAudio shutdown: %s did not finish within %.3fs; "
                "leaving the native call on a daemon thread so application shutdown can continue",
                stage,
                self.shutdown_timeout,
            )
            return

        with self._state_lock:
            if self.stream is stream:
                self.stream = None

    def join(self, timeout: float | None = None) -> None:
        return None

    def is_alive(self) -> bool:
        stream = self.stream
        if stream is None:
            return False
        try:
            return bool(stream.active)
        except Exception:
            return False

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
