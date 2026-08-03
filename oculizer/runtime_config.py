"""Application-level runtime configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "oculizer.json"


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
    return config


def configured_audio_input(config: dict[str, Any]) -> str | int:
    """Return the configured input selector, defaulting to the OS input."""
    return config.get("audio", {}).get("input_device", "default")
