"""Logical Oculizer scene to QLC+ OSC control mapping."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class SceneMapError(ValueError):
    """Raised when the logical QLC+ scene map is invalid."""


@dataclass(frozen=True)
class SceneControl:
    osc_path: str | None = None
    osc_action: str = "pushButton"
    caption: str | None = None


@dataclass(frozen=True)
class SceneMap:
    scenes: Mapping[str, SceneControl]
    pulse_seconds: float = 0.1
    unmapped: str = "ignore"
    fallback_scene: str | None = None

    @classmethod
    def from_file(cls, path: str | Path) -> "SceneMap":
        scene_map_path = Path(path).expanduser()
        try:
            with scene_map_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError as exc:
            raise SceneMapError(f"QLC+ scene map not found: {scene_map_path}") from exc
        except json.JSONDecodeError as exc:
            raise SceneMapError(f"Invalid JSON in QLC+ scene map {scene_map_path}: {exc}") from exc
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SceneMap":
        if not isinstance(data, Mapping):
            raise SceneMapError("QLC+ scene map must be a JSON object")
        raw_scenes = data.get("scenes", {})
        if not isinstance(raw_scenes, Mapping):
            raise SceneMapError("QLC+ scene map 'scenes' must be an object")

        pulse_seconds = data.get("pulse_seconds", 0.1)
        if isinstance(pulse_seconds, bool) or not isinstance(pulse_seconds, (int, float)):
            raise SceneMapError("pulse_seconds must be numeric")
        if not 0.0 <= float(pulse_seconds) <= 2.0:
            raise SceneMapError("pulse_seconds must be between 0 and 2 seconds")

        unmapped = data.get("unmapped", "ignore")
        if unmapped not in {"ignore", "error", "fallback"}:
            raise SceneMapError("unmapped must be 'ignore', 'error', or 'fallback'")

        scenes = {}
        for name, raw_control in raw_scenes.items():
            if not isinstance(name, str) or not name.strip():
                raise SceneMapError("scene names must be non-empty strings")
            if not isinstance(raw_control, Mapping):
                raise SceneMapError(f"scene '{name}' control must be an object")
            legacy_keys = sorted(set(raw_control).intersection({"action", "path"}))
            if legacy_keys:
                raise SceneMapError(
                    f"scene '{name}' uses obsolete keys {', '.join(legacy_keys)}; "
                    "use OSCaction and OSCPath"
                )
            osc_action = raw_control.get("OSCaction", "pushButton")
            if osc_action != "pushButton":
                raise SceneMapError(
                    f"scene '{name}' has unsupported OSCaction '{osc_action}'"
                )
            osc_path = raw_control.get("OSCPath")
            caption = raw_control.get("caption", name)
            if not isinstance(caption, str) or not caption.strip():
                raise SceneMapError(f"scene '{name}' caption must be a non-empty string")
            if not isinstance(osc_path, str) or not osc_path.startswith("/"):
                raise SceneMapError(
                    f"scene '{name}' OSCaction '{osc_action}' requires OSCPath starting with '/'"
                )
            scenes[name] = SceneControl(
                osc_path=osc_path,
                osc_action=osc_action,
                caption=caption,
            )

        fallback_scene = data.get("fallback_scene")
        if unmapped == "fallback":
            if not isinstance(fallback_scene, str) or fallback_scene not in scenes:
                raise SceneMapError("fallback_scene must name a mapped scene when unmapped is 'fallback'")
        elif fallback_scene is not None:
            raise SceneMapError("fallback_scene requires unmapped to be 'fallback'")

        return cls(
            scenes=scenes,
            pulse_seconds=float(pulse_seconds),
            unmapped=unmapped,
            fallback_scene=fallback_scene,
        )

    def get(self, scene_name: str) -> SceneControl | None:
        return self.scenes.get(scene_name)

    def resolve(self, scene_name: str) -> str | None:
        if scene_name in self.scenes:
            return scene_name
        if self.unmapped == "fallback":
            return self.fallback_scene
        return None
