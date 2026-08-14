"""Lightweight logical-scene state for the QLC+ Native runtime."""

from __future__ import annotations

import logging
from pathlib import Path

from oculizer.light.qlc_config import QLCConfig
from oculizer.runtime_config import DEFAULT_CONFIG_PATH


class LogicalSceneRegistry:
    """Expose scene selection from compact application metadata."""

    def __init__(self, config_path: str | Path | None = None):
        self.config_path = (
            Path(config_path).expanduser() if config_path is not None else DEFAULT_CONFIG_PATH
        )
        self.scenes: dict[str, dict] = {}
        self.current_scene: dict = {}
        self.reload_scenes()

    @staticmethod
    def _scene_entry(name: str, metadata: dict) -> dict:
        return {"name": name, **metadata}

    def _load_scenes(self) -> dict[str, dict]:
        config = QLCConfig.from_file(self.config_path)
        return {
            name: self._scene_entry(name, dict(metadata))
            for name, metadata in config.scene_metadata.items()
        }

    def reload_scenes(self):
        """Reload metadata atomically and preserve the selected logical name."""
        previous_name = self.current_scene.get("name")
        scenes = self._load_scenes()
        if not scenes:
            raise ValueError("The application configuration contains no logical scenes")

        if previous_name in scenes:
            selected = previous_name
        elif "party" in scenes:
            selected = "party"
        else:
            selected = next(iter(scenes))
        self.scenes = scenes
        self.current_scene = scenes[selected]
        if previous_name is None:
            logging.info("Defaulting to '%s' logical scene", selected)
        elif previous_name != selected:
            logging.warning(
                "Previous logical scene '%s' is unavailable; selected '%s'",
                previous_name,
                selected,
            )

    def set_scene(self, scene_name: str, apply_fallback: bool = False):
        """Select an existing logical scene; native QLC+ owns physical behavior."""
        if scene_name not in self.scenes:
            raise ValueError(f"Scene '{scene_name}' not found")
        self.current_scene = self.scenes[scene_name]

    def resolve_scene(self, scene_name: str, apply_fallback: bool = False) -> str:
        if scene_name not in self.scenes:
            raise ValueError(f"Scene '{scene_name}' not found")
        return scene_name
