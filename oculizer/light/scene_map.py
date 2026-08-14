"""Logical scene-to-caption routing for QLC+ Native."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class SceneMapError(ValueError):
    """Raised when native logical routing is invalid."""


@dataclass(frozen=True)
class SceneControl:
    caption: str


@dataclass(frozen=True)
class SceneMap:
    scenes: Mapping[str, SceneControl]
    pulse_seconds: float = 0.1
    fallback_scene: str | None = None

    @classmethod
    def from_native_mapping(
        cls,
        routing: Mapping[str, Any],
        scene_metadata: Mapping[str, Any],
    ) -> "SceneMap":
        if not isinstance(routing, Mapping):
            raise SceneMapError("lighting.routing must be an object")
        if not isinstance(scene_metadata, Mapping) or not scene_metadata:
            raise SceneMapError("lighting.scene_metadata must be a non-empty object")

        pulse_seconds = routing.get("pulse_seconds", 0.1)
        if isinstance(pulse_seconds, bool) or not isinstance(pulse_seconds, (int, float)):
            raise SceneMapError("lighting.routing.pulse_seconds must be numeric")
        if not 0.0 <= float(pulse_seconds) <= 2.0:
            raise SceneMapError("lighting.routing.pulse_seconds must be between 0 and 2 seconds")

        overrides = routing.get("caption_overrides", {})
        if not isinstance(overrides, Mapping):
            raise SceneMapError("lighting.routing.caption_overrides must be an object")
        unknown = set(overrides).difference(scene_metadata)
        if unknown:
            raise SceneMapError(
                "lighting.routing.caption_overrides contains unknown scenes: "
                + ", ".join(sorted(unknown))
            )

        scenes = {}
        for name in scene_metadata:
            if not isinstance(name, str) or not name.strip():
                raise SceneMapError("scene names must be non-empty strings")
            caption = overrides.get(name, name)
            if not isinstance(caption, str) or not caption.strip():
                raise SceneMapError(f"scene '{name}' caption must be a non-empty string")
            scenes[name] = SceneControl(caption=caption)

        fallback = routing.get("fallback_scene")
        if fallback is not None and fallback not in scenes:
            raise SceneMapError("lighting.routing.fallback_scene must name a known scene")
        return cls(scenes, float(pulse_seconds), fallback)

    def get(self, scene_name: str | None) -> SceneControl | None:
        return self.scenes.get(scene_name) if scene_name is not None else None

    def resolve(self, scene_name: str) -> str | None:
        if scene_name in self.scenes:
            return scene_name
        return self.fallback_scene
