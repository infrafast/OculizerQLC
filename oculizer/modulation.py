"""Rate-limited continuous lighting modulation derived from live audio."""

from __future__ import annotations

import logging
import time

from oculizer.runtime_config import MasterModulationConfig


logger = logging.getLogger(__name__)


class MasterModulator:
    """Map audio RMS to one smoothed, normalized lighting parameter."""

    def __init__(self, oculizer, config=None, clock=None):
        self.oculizer = oculizer
        self.config = config or MasterModulationConfig()
        self.clock = clock or time.monotonic
        self.last_update_at: float | None = None
        self.last_sent_value: float | None = None
        self.smoothed_value: float | None = None

    def _normalize(self, rms: float) -> float:
        if rms <= self.config.input_floor:
            return self.config.silence_value
        normalized = (rms - self.config.input_floor) / (
            self.config.input_ceiling - self.config.input_floor
        )
        return max(0.0, min(1.0, normalized))

    def update(self) -> bool:
        if not self.config.enabled:
            return False
        rms = getattr(self.oculizer, "current_audio_rms", None)
        if rms is None:
            return False
        now = self.clock()
        if self.last_update_at is not None and now - self.last_update_at < 1.0 / self.config.rate_hz:
            return False
        self.last_update_at = now

        target = self._normalize(float(rms))
        if self.smoothed_value is None or target == self.config.silence_value:
            self.smoothed_value = target
        else:
            alpha = self.config.smoothing_factor
            self.smoothed_value += alpha * (target - self.smoothed_value)
        value = max(0.0, min(1.0, self.smoothed_value))
        if self.last_sent_value is not None and abs(value - self.last_sent_value) < self.config.change_threshold:
            return False
        if not self.oculizer.set_parameter(self.config.parameter, value):
            return False
        self.last_sent_value = value
        return True

    def shutdown(self) -> bool:
        if not self.config.enabled:
            return False
        value = self.config.shutdown_value
        success = self.oculizer.set_parameter(self.config.parameter, value)
        if success:
            self.last_sent_value = value
            self.smoothed_value = value
        else:
            logger.warning("Failed to send safe master value during shutdown")
        return success
