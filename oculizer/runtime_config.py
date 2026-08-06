"""Application-level runtime configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "oculizer.json"


@dataclass(frozen=True)
class SilenceConfig:
    enabled: bool = True
    threshold: float = 0.001
    resume_threshold: float = 0.002
    duration_seconds: float = 2.0
    scene: str = "off"

@dataclass(frozen=True)
class SpeechConfig:
    enabled: bool = True
    threshold: float = 0.55
    music_margin: float = 0.15
    minimum_duration_seconds: float = 1.0
    release_duration_seconds: float = 2.0
    scene: str = "announcement"

@dataclass(frozen=True)
class PredictionConfig:
    window_seconds: float = 2.0

@dataclass(frozen=True)
class MasterModulationConfig:
    enabled: bool = False
    parameter: str = "master"
    rate_hz: float = 25.0
    input_floor: float = 0.001
    input_ceiling: float = 0.1
    smoothing_factor: float = 0.25
    change_threshold: float = 0.01
    silence_value: float = 0.0
    shutdown_value: float = 0.0
    refresh_seconds: float = 1.0

@dataclass(frozen=True)
class FrequencyBandConfig:
    enabled: bool
    parameter: str
    low_hz: float
    high_hz: float
    input_floor: float
    input_ceiling: float
    response: str = "level"
    baseline_smoothing: float = 0.02

@dataclass(frozen=True)
class FrequencyModulationConfig:
    enabled: bool = False
    rate_hz: float = 25.0
    smoothing_factor: float = 0.3
    change_threshold: float = 0.02
    silence_value: float = 0.0
    shutdown_value: float = 0.0
    refresh_seconds: float = 1.0
    bands: Mapping[str, FrequencyBandConfig] | None = None

DEFAULT_FREQUENCY_BANDS = {
    "bass": FrequencyBandConfig(True, "bass", 35.0, 180.0, 0.0001, 0.02, "transient", 0.02),
    "mid": FrequencyBandConfig(False, "mid", 180.0, 2000.0, 0.0001, 0.02),
    "high": FrequencyBandConfig(False, "high", 2000.0, 8000.0, 0.0001, 0.02),
}


def load_runtime_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load and minimally validate the general Oculizer JSON configuration."""
    config_path = Path(path).expanduser() if path is not None else DEFAULT_CONFIG_PATH
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"Oculizer configuration not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in Oculizer configuration {config_path}: {exc}") from exc

    if not isinstance(config, dict):
        raise ValueError("Oculizer configuration must be a JSON object")
    audio = config.get("audio", {})
    if not isinstance(audio, dict):
        raise ValueError("Oculizer configuration 'audio' must be an object")
    input_device = audio.get("input_device", "default")
    if not isinstance(input_device, (str, int)) or isinstance(input_device, bool):
        raise ValueError("audio.input_device must be 'default', a device name, or an index")
    silence = audio.get("silence", {})
    if not isinstance(silence, dict):
        raise ValueError("audio.silence must be an object")
    silence_config = SilenceConfig(
        enabled=silence.get("enabled", True),
        threshold=silence.get("threshold", 0.001),
        resume_threshold=silence.get("resume_threshold", 0.002),
        duration_seconds=silence.get("duration_seconds", 2.0),
        scene=silence.get("scene", "off"),
    )
    if not isinstance(silence_config.enabled, bool):
        raise ValueError("audio.silence.enabled must be a boolean")
    for field_name in ("threshold", "resume_threshold", "duration_seconds"):
        value = getattr(silence_config, field_name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"audio.silence.{field_name} must be a non-negative number")
    if silence_config.resume_threshold <= silence_config.threshold:
        raise ValueError("audio.silence.resume_threshold must be greater than threshold")
    if not isinstance(silence_config.scene, str) or not silence_config.scene.strip():
        raise ValueError("audio.silence.scene must be a non-empty string")
    speech = audio.get("speech", {})
    if not isinstance(speech, dict):
        raise ValueError("audio.speech must be an object")
    speech_config = SpeechConfig(**{k: speech.get(k, getattr(SpeechConfig(), k)) for k in SpeechConfig.__dataclass_fields__})
    if not isinstance(speech_config.enabled, bool) or not isinstance(speech_config.scene, str):
        raise ValueError("invalid audio.speech configuration")
    for name in ("threshold", "music_margin", "minimum_duration_seconds", "release_duration_seconds"):
        value = getattr(speech_config, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"audio.speech.{name} must be a non-negative number")
    prediction = audio.get("prediction", {})
    if not isinstance(prediction, dict):
        raise ValueError("audio.prediction must be an object")
    window_seconds = prediction.get("window_seconds", 2.0)
    if isinstance(window_seconds, bool) or not isinstance(window_seconds, (int, float)) or not 0.5 <= window_seconds <= 10:
        raise ValueError("audio.prediction.window_seconds must be between 0.5 and 10 seconds")
    master = audio.get("master_modulation", {})
    if not isinstance(master, dict):
        raise ValueError("audio.master_modulation must be an object")
    defaults = MasterModulationConfig()
    master_config = MasterModulationConfig(**{
        key: master.get(key, getattr(defaults, key))
        for key in MasterModulationConfig.__dataclass_fields__
    })
    if not isinstance(master_config.enabled, bool):
        raise ValueError("audio.master_modulation.enabled must be a boolean")
    if not isinstance(master_config.parameter, str) or not master_config.parameter.strip():
        raise ValueError("audio.master_modulation.parameter must be a non-empty string")
    for name in ("rate_hz", "input_floor", "input_ceiling", "smoothing_factor", "change_threshold", "silence_value", "shutdown_value", "refresh_seconds"):
        value = getattr(master_config, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"audio.master_modulation.{name} must be numeric")
    if not 1 <= master_config.rate_hz <= 60:
        raise ValueError("audio.master_modulation.rate_hz must be between 1 and 60")
    if master_config.input_floor < 0 or master_config.input_ceiling <= master_config.input_floor:
        raise ValueError("audio.master_modulation.input_ceiling must be greater than input_floor")
    for name in ("smoothing_factor", "change_threshold", "silence_value", "shutdown_value"):
        if not 0 <= getattr(master_config, name) <= 1:
            raise ValueError(f"audio.master_modulation.{name} must be between 0 and 1")
    if master_config.refresh_seconds <= 0:
        raise ValueError("audio.master_modulation.refresh_seconds must be greater than zero")
    _parse_frequency_modulation(audio.get("frequency_modulation", {}))
    return config

def _parse_frequency_modulation(raw: Any) -> FrequencyModulationConfig:
    if not isinstance(raw, dict):
        raise ValueError("audio.frequency_modulation must be an object")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("audio.frequency_modulation.enabled must be a boolean")
    numeric_defaults = {
        "rate_hz": 25.0,
        "smoothing_factor": 0.3,
        "change_threshold": 0.02,
        "silence_value": 0.0,
        "shutdown_value": 0.0,
        "refresh_seconds": 1.0,
    }
    numeric = {}
    for name, default in numeric_defaults.items():
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"audio.frequency_modulation.{name} must be numeric")
        numeric[name] = float(value)
    if not 1 <= numeric["rate_hz"] <= 60:
        raise ValueError("audio.frequency_modulation.rate_hz must be between 1 and 60")
    for name in ("smoothing_factor", "change_threshold", "silence_value", "shutdown_value"):
        if not 0 <= numeric[name] <= 1:
            raise ValueError(f"audio.frequency_modulation.{name} must be between 0 and 1")
    if numeric["refresh_seconds"] <= 0:
        raise ValueError("audio.frequency_modulation.refresh_seconds must be greater than zero")

    raw_bands = raw.get("bands", {})
    if not isinstance(raw_bands, dict):
        raise ValueError("audio.frequency_modulation.bands must be an object")
    bands = {}
    for name, default in DEFAULT_FREQUENCY_BANDS.items():
        data = raw_bands.get(name, {})
        if not isinstance(data, dict):
            raise ValueError(f"audio.frequency_modulation.bands.{name} must be an object")
        band = FrequencyBandConfig(**{
            field: data.get(field, getattr(default, field))
            for field in FrequencyBandConfig.__dataclass_fields__
        })
        if not isinstance(band.enabled, bool) or not isinstance(band.parameter, str) or not band.parameter.strip():
            raise ValueError(f"invalid audio.frequency_modulation.bands.{name} configuration")
        if band.response not in {"level", "transient"}:
            raise ValueError(f"audio.frequency_modulation.bands.{name}.response must be 'level' or 'transient'")
        for field in ("low_hz", "high_hz", "input_floor", "input_ceiling", "baseline_smoothing"):
            value = getattr(band, field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"audio.frequency_modulation.bands.{name}.{field} must be numeric")
        if band.low_hz < 0 or band.high_hz <= band.low_hz:
            raise ValueError(f"audio.frequency_modulation.bands.{name}.high_hz must be greater than low_hz")
        if band.input_floor < 0 or band.input_ceiling <= band.input_floor:
            raise ValueError(f"audio.frequency_modulation.bands.{name}.input_ceiling must be greater than input_floor")
        if not 0 < band.baseline_smoothing <= 1:
            raise ValueError(f"audio.frequency_modulation.bands.{name}.baseline_smoothing must be between 0 and 1")
        bands[name] = FrequencyBandConfig(
            band.enabled,
            band.parameter,
            float(band.low_hz),
            float(band.high_hz),
            float(band.input_floor),
            float(band.input_ceiling),
            band.response,
            float(band.baseline_smoothing),
        )
    return FrequencyModulationConfig(enabled=enabled, bands=bands, **numeric)


def configured_audio_input(config: dict[str, Any]) -> str | int:
    """Return the configured input selector, defaulting to the OS input."""
    return config.get("audio", {}).get("input_device", "default")


def configured_silence(config: dict[str, Any]) -> SilenceConfig:
    """Return the validated silence routing policy."""
    silence = config.get("audio", {}).get("silence", {})
    return SilenceConfig(
        enabled=silence.get("enabled", True),
        threshold=float(silence.get("threshold", 0.001)),
        resume_threshold=float(silence.get("resume_threshold", 0.002)),
        duration_seconds=float(silence.get("duration_seconds", 2.0)),
        scene=silence.get("scene", "off"),
    )

def configured_speech(config: dict[str, Any]) -> SpeechConfig:
    speech = config.get("audio", {}).get("speech", {})
    defaults = SpeechConfig()
    return SpeechConfig(**{k: speech.get(k, getattr(defaults, k)) for k in SpeechConfig.__dataclass_fields__})

def configured_prediction(config: dict[str, Any]) -> PredictionConfig:
    prediction = config.get("audio", {}).get("prediction", {})
    return PredictionConfig(window_seconds=float(prediction.get("window_seconds", 2.0)))

def configured_master_modulation(config: dict[str, Any]) -> MasterModulationConfig:
    master = config.get("audio", {}).get("master_modulation", {})
    defaults = MasterModulationConfig()
    values = {
        key: master.get(key, getattr(defaults, key))
        for key in MasterModulationConfig.__dataclass_fields__
    }
    for key in ("rate_hz", "input_floor", "input_ceiling", "smoothing_factor", "change_threshold", "silence_value", "shutdown_value", "refresh_seconds"):
        values[key] = float(values[key])
    return MasterModulationConfig(**values)

def configured_frequency_modulation(config: dict[str, Any]) -> FrequencyModulationConfig:
    return _parse_frequency_modulation(config.get("audio", {}).get("frequency_modulation", {}))


def configured_dynamic_controls(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return validated named dynamic-control profiles."""
    defaults = {
        "responsive": {"cache": 3, "rate": (10, 10.0), "throttle": (4, 1.0)},
        "normal": {"cache": 15, "rate": (4, 15.0), "throttle": (2, 4.0)},
        "calm": {"cache": 35, "rate": (2, 20.0), "throttle": None},
    }
    configured = config.get("control", {}).get("dynamic_controls", defaults)
    if not isinstance(configured, dict):
        raise ValueError("control.dynamic_controls must be an object")
    result = {}
    for name, values in configured.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(values, dict):
            raise ValueError("each dynamic control must have a non-empty name and object value")
        if name.casefold() == "off":
            raise ValueError("control.dynamic_controls cannot redefine the reserved 'off' profile")
        cache = values.get("cache")
        if isinstance(cache, bool) or not isinstance(cache, int) or not 1 <= cache <= 100:
            raise ValueError(f"control.dynamic_controls.{name}.cache must be between 1 and 100")
        parsed = {"cache": cache}
        for key in ("rate", "throttle"):
            value = values.get(key)
            if value is None:
                parsed[key] = None
                continue
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ValueError(f"control.dynamic_controls.{name}.{key} must be [count, seconds] or null")
            count, seconds = value
            if (isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100
                    or isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                    or not 0.5 <= seconds <= 300):
                raise ValueError(f"control.dynamic_controls.{name}.{key} values are out of range")
            parsed[key] = (count, float(seconds))
        result[name] = parsed
    return result
