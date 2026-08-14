"""Validated QLC+ Native section of the application configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from oculizer.light.scene_map import SceneMap, SceneMapError
from oculizer.light.qlc_native import QLCNativeConfig


class QLCConfigError(ValueError):
    """Raised when native lighting configuration is invalid."""


@dataclass(frozen=True)
class QLCControl:
    caption: str


@dataclass(frozen=True)
class QLCConfig:
    native: QLCNativeConfig
    controls: Mapping[str, QLCControl]
    routing: SceneMap
    scene_metadata: Mapping[str, Mapping[str, Any]]

    @classmethod
    def from_file(cls, path: str | Path) -> "QLCConfig":
        config_path = Path(path).expanduser()
        try:
            with config_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError as exc:
            raise QLCConfigError(f"QLC+ configuration not found: {config_path}") from exc
        except json.JSONDecodeError as exc:
            raise QLCConfigError(f"Invalid JSON in QLC+ configuration {config_path}: {exc}") from exc
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "QLCConfig":
        if not isinstance(data, Mapping):
            raise QLCConfigError("QLC+ configuration must be a JSON object")

        if "lighting" not in data:
            raise QLCConfigError("Application configuration requires a 'lighting' object")
        return cls._from_application_mapping(data)

    @classmethod
    def _from_application_mapping(cls, data: Mapping[str, Any]) -> "QLCConfig":
        lighting = data.get("lighting")
        if not isinstance(lighting, Mapping):
            raise QLCConfigError("Application configuration 'lighting' must be an object")
        native = lighting.get("native", {})
        controls = lighting.get("controls", {})
        routing = lighting.get("routing", {})
        metadata = lighting.get("scene_metadata", {})
        if not isinstance(native, Mapping):
            raise QLCConfigError("lighting.native must be an object")
        if not isinstance(controls, Mapping):
            raise QLCConfigError("lighting.controls must be an object")
        if not isinstance(metadata, Mapping):
            raise QLCConfigError("lighting.scene_metadata must be an object")

        validated_controls = {}
        for name, caption in controls.items():
            if not isinstance(name, str) or not name.strip():
                raise QLCConfigError("lighting control names must be non-empty strings")
            if not isinstance(caption, str) or not caption.strip():
                raise QLCConfigError(f"lighting control '{name}' caption must be non-empty")
            validated_controls[name] = QLCControl(caption=caption)

        validated_metadata = {}
        for name, raw_metadata in metadata.items():
            if not isinstance(raw_metadata, Mapping):
                raise QLCConfigError(f"scene metadata '{name}' must be an object")
            description = raw_metadata.get("description")
            behavior = raw_metadata.get("design_behavior")
            if not isinstance(description, str) or not description.strip():
                raise QLCConfigError(f"scene metadata '{name}' requires a description")
            if behavior not in {"static", "normal", "responsive"}:
                raise QLCConfigError(
                    f"scene metadata '{name}' design_behavior must be static, normal, or responsive"
                )
            duration = raw_metadata.get("max_duration_seconds")
            if duration is not None and (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not 0.5 <= float(duration) <= 3600.0
            ):
                raise QLCConfigError(
                    f"scene metadata '{name}' max_duration_seconds must be between 0.5 and 3600"
                )
            validated_metadata[name] = dict(raw_metadata)

        try:
            return cls(
                native=QLCNativeConfig.from_mapping(native),
                controls=validated_controls,
                routing=SceneMap.from_native_mapping(routing, validated_metadata),
                scene_metadata=validated_metadata,
            )
        except (SceneMapError, ValueError) as exc:
            raise QLCConfigError(str(exc)) from exc
