"""Interchangeable lighting output backends for Oculizer."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from oculizer.light.osc_client import OscClient, OscConfig
from oculizer.light.scene_map import SceneMap


logger = logging.getLogger(__name__)

OUTPUT_ENTTEC = "enttec"
OUTPUT_QLC_OSC = "qlc-osc"
OUTPUT_DISABLED = "disabled"
OUTPUT_CHOICES = (OUTPUT_ENTTEC, OUTPUT_QLC_OSC)


class LightingBackend(ABC):
    """Intent-level interface shared by all lighting outputs."""

    name: str
    supports_direct_fixture_output: bool = False

    def resolve_scene(self, scene_name: str) -> str | None:
        return scene_name

    @abstractmethod
    def activate_scene(self, scene_name: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def deactivate_scene(self, scene_name: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def set_parameter(self, name: str, value: float) -> bool:
        raise NotImplementedError

    @abstractmethod
    def blackout(self, enabled: bool = True) -> bool:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class DisabledBackend(LightingBackend):
    """No-output backend used by prediction-only test mode."""

    name = OUTPUT_DISABLED

    def activate_scene(self, scene_name: str) -> bool:
        return True

    def deactivate_scene(self, scene_name: str) -> bool:
        return True

    def set_parameter(self, name: str, value: float) -> bool:
        return True

    def blackout(self, enabled: bool = True) -> bool:
        return True

    def close(self) -> None:
        return None


class EnttecBackend(LightingBackend):
    """Adapter around the existing direct-DMX controller and fixtures."""

    name = OUTPUT_ENTTEC
    supports_direct_fixture_output = True

    def __init__(self, controller: Any, fixtures: Mapping[str, Any]):
        self.controller = controller
        self.fixtures = dict(fixtures)
        self._closed = False

    def activate_scene(self, scene_name: str) -> bool:
        # Direct DMX scenes are rendered by Oculizer's existing mapping loop.
        return True

    def deactivate_scene(self, scene_name: str) -> bool:
        return True

    def set_parameter(self, name: str, value: float) -> bool:
        logger.debug("Enttec backend ignores intent parameter %s", name)
        return False

    def blackout(self, enabled: bool = True) -> bool:
        if self._closed:
            return False
        if enabled:
            self.controller.blackout()
        return True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.controller.close()


class QLCOscBackend(LightingBackend):
    """QLC+ intent backend backed by the reusable OSC client."""

    name = OUTPUT_QLC_OSC

    def __init__(self, client: OscClient, scene_map: SceneMap, scene_map_path: str | Path | None = None):
        self.client = client
        self.scene_map = scene_map
        self.scene_map_path = Path(scene_map_path) if scene_map_path is not None else None
        self.active_scene: str | None = None

    def reload_scene_map(self) -> None:
        if self.scene_map_path is None:
            return
        self.scene_map = SceneMap.from_file(self.scene_map_path)

    def _pulse(self, path: str) -> bool:
        pressed = self.client.press(path)
        if self.scene_map.pulse_seconds:
            time.sleep(self.scene_map.pulse_seconds)
        released = self.client.release(path)
        return pressed and released

    def resolve_scene(self, scene_name: str) -> str | None:
        return self.scene_map.resolve(scene_name)

    def activate_scene(self, scene_name: str) -> bool:
        requested_scene = scene_name
        scene_name = self.resolve_scene(scene_name)
        control = self.scene_map.get(scene_name) if scene_name is not None else None
        if control is None:
            message = f"QLC+ scene '{requested_scene}' has no OSC mapping"
            if self.scene_map.unmapped == "error":
                raise KeyError(message)
            logger.warning(message)
            return False
        if requested_scene != scene_name:
            logger.info("QLC+ scene request '%s' resolved to fallback '%s'", requested_scene, scene_name)
        if scene_name == self.active_scene:
            logger.debug("QLC+ scene '%s' is already active", scene_name)
            return True

        if self.active_scene is not None and not self.deactivate_scene(self.active_scene):
            return False

        if control.action == "off":
            self.active_scene = None
            return True
        if control.action == "blackout":
            success = self.blackout(True)
        else:
            success = self._pulse(control.path)
        if success:
            self.active_scene = scene_name
        return success

    def deactivate_scene(self, scene_name: str) -> bool:
        if scene_name != self.active_scene:
            return True
        control = self.scene_map.get(scene_name)
        if control is None:
            logger.warning("Cannot deactivate unmapped QLC+ scene '%s'", scene_name)
            return False
        if control.action == "toggle":
            success = self._pulse(control.path)
        elif control.action == "blackout":
            success = self.blackout(False)
        else:
            success = True
        if success:
            self.active_scene = None
        return success

    def set_parameter(self, name: str, value: float) -> bool:
        address = name if name.startswith("/") else f"/oculizer/{name}"
        return self.client.set_level(address, value)

    def blackout(self, enabled: bool = True) -> bool:
        return self.client.blackout(enabled)

    def close(self) -> None:
        self.client.close()


def create_qlc_osc_backend(
    config_path: str | Path,
    scene_map_path: str | Path,
    *,
    host: str | None = None,
    port: int | None = None,
    dry_run: bool | None = None,
) -> QLCOscBackend:
    """Create a QLC+ backend with optional command-line overrides."""
    config = OscConfig.from_file(config_path)
    overrides = {}
    if host is not None:
        overrides["host"] = host
    if port is not None:
        overrides["port"] = port
    if dry_run is not None:
        overrides["dry_run"] = dry_run
    if overrides:
        config = replace(config, **overrides)
        config.validate()
    return QLCOscBackend(
        OscClient(config),
        SceneMap.from_file(scene_map_path),
        scene_map_path=scene_map_path,
    )
