"""Native-only logical lighting controller for QLC+ 5."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from oculizer.light.qlc_config import QLCConfig
from oculizer.light.qlc_native import QLCNativeClient


logger = logging.getLogger(__name__)


class DisabledLightingController:
    """No-output controller used only by prediction test mode."""

    scene_metadata = {}
    supports_direct_fixture_output = False

    def resolve_scene(self, scene_name):
        return scene_name

    def activate_scene(self, scene_name):
        return True

    def set_parameter(self, name, value):
        return True

    def reload_scene_map(self):
        return None

    def close(self):
        return None


class NativeLightingController:
    """Resolve logical names to the authoritative QLC+ project inventory."""

    supports_direct_fixture_output = False

    def __init__(self, client, scene_map, controls, config_path, scene_metadata):
        self.client = client
        self.scene_map = scene_map
        self.controls = dict(controls)
        self.config_path = Path(config_path)
        self.scene_metadata = dict(scene_metadata)
        self.active_scene = None
        self._closed = False

    def start(self):
        self.client.start()

    def resolve_scene(self, scene_name):
        return self.scene_map.resolve(scene_name)

    def activate_scene(self, scene_name):
        requested = scene_name
        target = self.resolve_scene(scene_name)
        control = self.scene_map.get(target) if target is not None else None
        if control is None:
            logger.warning("QLC+ scene '%s' has no native route", requested)
            return False
        if target != requested:
            logger.info("QLC+ scene request '%s' resolved to fallback '%s'", requested, target)
        if target == self.active_scene:
            return True
        if not self.client.activate_button(control.caption):
            return False
        self.active_scene = target
        return True

    def set_parameter(self, name, value):
        control = self.controls.get(name)
        if control is None:
            logger.warning("QLC+ continuous control '%s' is not configured", name)
            return False
        return self.client.set_slider_level(control.caption, value)

    def reload_scene_map(self):
        config = QLCConfig.from_file(self.config_path)
        self.scene_map = config.routing
        self.controls = dict(config.controls)
        self.scene_metadata = dict(config.scene_metadata)
        self.active_scene = None

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.client.close()


def create_native_lighting_controller(
    config_path, *, host=None, port=None, dry_run=None, encryption_key=None,
):
    config = QLCConfig.from_file(config_path)
    native = config.native
    overrides = {}
    if host is not None:
        overrides["host"] = host
    if port is not None:
        overrides["port"] = port
    if dry_run is not None:
        overrides["dry_run"] = dry_run
    if encryption_key is not None:
        overrides["encryption_key"] = encryption_key
    if overrides:
        native = replace(native, **overrides)
        native.validate()

    controller = NativeLightingController(
        QLCNativeClient(
            native.host,
            native.port,
            native.encryption_key,
            native.reconnect_seconds,
            native.maximum_project_size,
            native.dry_run,
            button_release_seconds=config.routing.pulse_seconds,
        ),
        config.routing,
        config.controls,
        config_path,
        config.scene_metadata,
    )
    controller.start()
    return controller
