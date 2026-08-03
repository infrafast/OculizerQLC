"""Rate-limited continuous lighting modulation derived from live audio."""

from __future__ import annotations

import logging
import time

import numpy as np
import librosa

from oculizer.runtime_config import FrequencyModulationConfig, MasterModulationConfig


logger = logging.getLogger(__name__)


def extract_frequency_bands(mel_power, sample_rate: float, bands) -> dict[str, float]:
    """Return RMS-like energy for configured ranges in an existing Mel spectrum."""
    values = np.asarray(mel_power, dtype=float)
    frequencies = librosa.mel_frequencies(n_mels=len(values), fmin=0.0, fmax=sample_rate / 2.0)
    result = {}
    for name, band in bands.items():
        mask = (frequencies >= band.low_hz) & (frequencies < band.high_hz)
        result[name] = float(np.sqrt(np.mean(values[mask]))) if np.any(mask) else 0.0
    return result


class MasterModulator:
    """Map audio RMS to one smoothed, normalized lighting parameter."""

    def __init__(self, oculizer, config=None, clock=None):
        self.oculizer = oculizer
        self.config = config or MasterModulationConfig()
        self.clock = clock or time.monotonic
        self.last_update_at: float | None = None
        self.last_sent_value: float | None = None
        self.last_sent_at: float | None = None
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
        refresh_due = self.last_sent_at is None or now - self.last_sent_at >= self.config.refresh_seconds
        if not refresh_due and self.last_sent_value is not None and abs(value - self.last_sent_value) < self.config.change_threshold:
            return False
        if not self.oculizer.set_parameter(self.config.parameter, value):
            return False
        self.last_sent_value = value
        self.last_sent_at = now
        return True

    def startup(self) -> bool:
        if not self.config.enabled:
            return False
        value = self.config.silence_value
        success = self.oculizer.set_parameter(self.config.parameter, value)
        if success:
            self.last_sent_value = value
            self.smoothed_value = value
            self.last_sent_at = self.clock()
        return success

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


class FrequencyBandModulator:
    """Send independently smoothed bass, mid, and high energy controls."""

    def __init__(self, oculizer, config=None, clock=None):
        self.oculizer = oculizer
        self.config = config or FrequencyModulationConfig()
        self.clock = clock or time.monotonic
        self.last_update_at: float | None = None
        self.last_sent_values: dict[str, float] = {}
        self.last_sent_at: dict[str, float] = {}
        self.smoothed_values: dict[str, float] = {}
        self.baselines: dict[str, float] = {}

    def update(self) -> bool:
        if not self.config.enabled:
            return False
        energies = getattr(self.oculizer, "current_frequency_bands", None)
        spectrum = getattr(self.oculizer, "current_mel_spectrum", None)
        sample_rate = getattr(self.oculizer, "current_mel_sample_rate", None)
        if spectrum is not None and sample_rate is not None:
            energies = extract_frequency_bands(spectrum, sample_rate, self.config.bands or {})
        if not energies:
            return False
        now = self.clock()
        if self.last_update_at is not None and now - self.last_update_at < 1.0 / self.config.rate_hz:
            return False
        self.last_update_at = now
        sent = False
        for name, band in (self.config.bands or {}).items():
            if not band.enabled or name not in energies:
                continue
            energy = float(energies[name])
            if band.response == "transient":
                baseline = self.baselines.get(name, 0.0)
                energy_above_baseline = max(0.0, energy - baseline)
                baseline += band.baseline_smoothing * (energy - baseline)
                self.baselines[name] = baseline
                energy = energy_above_baseline
            if energy <= band.input_floor:
                target = self.config.silence_value
            else:
                target = max(0.0, min(1.0, (energy - band.input_floor) / (band.input_ceiling - band.input_floor)))
            previous = self.smoothed_values.get(name)
            if previous is None or target == self.config.silence_value:
                value = target
            else:
                value = previous + self.config.smoothing_factor * (target - previous)
            self.smoothed_values[name] = value
            refresh_due = name not in self.last_sent_at or now - self.last_sent_at[name] >= self.config.refresh_seconds
            if not refresh_due and name in self.last_sent_values and abs(value - self.last_sent_values[name]) < self.config.change_threshold:
                continue
            if self.oculizer.set_parameter(band.parameter, value):
                self.last_sent_values[name] = value
                self.last_sent_at[name] = now
                sent = True
        return sent

    def startup(self) -> bool:
        if not self.config.enabled:
            return False
        success = True
        sent = False
        now = self.clock()
        for name, band in (self.config.bands or {}).items():
            if band.enabled:
                sent = True
                value = self.config.silence_value
                current_success = self.oculizer.set_parameter(band.parameter, value)
                success = current_success and success
                if current_success:
                    self.last_sent_values[name] = value
                    self.smoothed_values[name] = value
                    self.last_sent_at[name] = now
        return sent and success

    def shutdown(self) -> bool:
        if not self.config.enabled:
            return False
        success = True
        sent = False
        for band in (self.config.bands or {}).values():
            if band.enabled:
                sent = True
                success = self.oculizer.set_parameter(band.parameter, self.config.shutdown_value) and success
        return sent and success
