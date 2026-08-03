"""Unified QLC+ transport, global controls, and scene-routing configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from oculizer.light.osc_client import OscConfig, OscConfigError
from oculizer.light.scene_map import SceneMap, SceneMapError


class QLCConfigError(ValueError):
    """Raised when the unified QLC+ configuration is invalid."""


@dataclass(frozen=True)
class QLCConfig:
    transport: OscConfig
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
        routing = data.get("routing", {})
        if not isinstance(transport, Mapping):
            raise QLCConfigError("QLC+ configuration 'transport' must be an object")
        if not isinstance(controls, Mapping):
            raise QLCConfigError("QLC+ configuration 'controls' must be an object")
        if not isinstance(routing, Mapping):
            raise QLCConfigError("QLC+ configuration 'routing' must be an object")

        unknown_controls = set(controls) - {"blackout"}
        if unknown_controls:
            names = ", ".join(sorted(str(name) for name in unknown_controls))
            raise QLCConfigError(f"Unsupported QLC+ global control(s): {names}")

        osc_data = dict(transport)
        osc_data["paths"] = {"blackout": controls.get("blackout", OscConfig.blackout_path)}
        try:
            return cls(
                transport=OscConfig.from_mapping(osc_data),
                routing=SceneMap.from_mapping(routing),
            )
        except (OscConfigError, SceneMapError) as exc:
            raise QLCConfigError(str(exc)) from exc
