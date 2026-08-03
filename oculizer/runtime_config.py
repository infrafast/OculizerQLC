"""Application-level runtime configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "oculizer.json"


@dataclass(frozen=True)
class SilenceConfig:
    enabled: bool = True
    threshold: float = 0.001
    resume_threshold: float = 0.002
    duration_seconds: float = 2.0
    scene: str = "off"


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
    return config


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
