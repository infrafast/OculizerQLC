"""Hardware-free Enttec-compatible DMX frame sink for development."""

from __future__ import annotations

import logging
import time


logger = logging.getLogger(__name__)


class VirtualEnttecController:
    """Collect complete DMX frames and log rate-limited channel changes."""

    def __init__(self, log_rate_hz: float = 3.0, log_frames: bool = True, clock=None):
        if log_rate_hz <= 0:
            raise ValueError("DMX dry-run log rate must be greater than zero")
        self.dmx_data = [0] * 513
        self.log_rate_hz = float(log_rate_hz)
        self.log_frames = bool(log_frames)
        self.clock = clock or time.monotonic
        self._last_logged_frame = list(self.dmx_data)
        self._last_logged_at = None
        self._closed = False

    def _send_dmx_packet(self, force: bool = False):
        if self._closed or not self.log_frames:
            return
        now = self.clock()
        if (
            not force
            and self._last_logged_at is not None
            and now - self._last_logged_at < 1.0 / self.log_rate_hz
        ):
            return
        changed = {
            channel: value
            for channel, value in enumerate(self.dmx_data[1:], start=1)
            if value != self._last_logged_frame[channel]
        }
        if not changed:
            return
        logger.info("DMX dry-run: changed=%s", changed)
        self._last_logged_frame = list(self.dmx_data)
        self._last_logged_at = now

    def blackout(self):
        self.dmx_data[:] = [0] * 513
        self._send_dmx_packet()

    def set_channel(self, channel: int, value: int):
        if 1 <= channel <= 512:
            self.dmx_data[channel] = max(0, min(255, int(value)))
            self._send_dmx_packet()

    def set_channels(self, channels, values):
        if len(channels) != len(values):
            raise ValueError("Channels and values lists must have the same length")
        for channel, value in zip(channels, values):
            if 1 <= channel <= 512:
                self.dmx_data[channel] = max(0, min(255, int(value)))
        self._send_dmx_packet()

    def send_dmx(self, data, start_channel: int = 1):
        for offset, value in enumerate(data):
            channel = start_channel + offset
            if channel > 512:
                break
            if channel >= 1:
                self.dmx_data[channel] = max(0, min(255, int(value)))
        self._send_dmx_packet()

    def close(self):
        if self._closed:
            return
        self.dmx_data[:] = [0] * 513
        self._send_dmx_packet(force=True)
        self._closed = True
