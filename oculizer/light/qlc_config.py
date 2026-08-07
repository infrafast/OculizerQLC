"""Unified QLC+ transport, global controls, and scene-routing configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from oculizer.light.osc_client import OscConfig, OscConfigError, build_float_message
from oculizer.light.scene_map import SceneMap, SceneMapError
from oculizer.light.qlc_websocket import QLCWebSocketConfig


class QLCConfigError(ValueError):
    """Raised when the unified QLC+ configuration is invalid."""


@dataclass(frozen=True)
class QLCConfig:
    transport: OscConfig
    websocket: QLCWebSocketConfig
    controls: Mapping[str, str]
    routing: SceneMap

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

        transport = data.get("transport", {})
        controls = data.get("controls", {})
        websocket = data.get("websocket", {})
        routing = data.get("routing", {})
        if not isinstance(transport, Mapping):
            raise QLCConfigError("QLC+ configuration 'transport' must be an object")
        if not isinstance(controls, Mapping):
            raise QLCConfigError("QLC+ configuration 'controls' must be an object")
        if not isinstance(routing, Mapping):
            raise QLCConfigError("QLC+ configuration 'routing' must be an object")
        if not isinstance(websocket, Mapping):
            raise QLCConfigError("QLC+ configuration 'websocket' must be an object")

        validated_controls = {}
        for name, path in controls.items():
            if not isinstance(name, str) or not name.strip():
                raise QLCConfigError("QLC+ control names must be non-empty strings")
            try:
                build_float_message(path, 0.0)
            except (TypeError, ValueError) as exc:
                raise QLCConfigError(f"Invalid QLC+ control '{name}': {exc}") from exc
            validated_controls[name] = path

        osc_data = dict(transport)
        osc_data["paths"] = {"blackout": validated_controls.get("blackout", OscConfig.blackout_path)}
        try:
            return cls(
                transport=OscConfig.from_mapping(osc_data),
                websocket=QLCWebSocketConfig.from_mapping(websocket),
                controls=validated_controls,
                routing=SceneMap.from_mapping(routing),
            )
        except (OscConfigError, SceneMapError, ValueError) as exc:
            raise QLCConfigError(str(exc)) from exc
