"""Interchangeable lighting output backends for Oculizer."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from oculizer.light.osc_client import OscClient
from oculizer.light.qlc_config import QLCConfig
from oculizer.light.scene_map import SceneMap
from oculizer.light.qlc_websocket import QLCWebSocketClient, QLCWebSocketError
from oculizer.light.qlc_native import QLCNativeClient


logger = logging.getLogger(__name__)

OUTPUT_ENTTEC = "enttec"
OUTPUT_QLC_OSC = "qlc-osc"
OUTPUT_QLC_WEBSOCKET = "qlc-websocket"
OUTPUT_QLC_NATIVE = "qlc-native"
OUTPUT_DISABLED = "disabled"
OUTPUT_CHOICES = (OUTPUT_ENTTEC, OUTPUT_QLC_OSC, OUTPUT_QLC_WEBSOCKET, OUTPUT_QLC_NATIVE)


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

    def __init__(self, client: OscClient, scene_map: SceneMap, controls=None, config_path: str | Path | None = None):
        self.client = client
        self.scene_map = scene_map
        self.controls = dict(controls or {})
        self.config_path = Path(config_path) if config_path is not None else None
        self.active_scene: str | None = None
        self._closed = False

    def initialize(self) -> bool:
        return True

    def reload_scene_map(self) -> None:
        if self.config_path is None:
            return
        config = QLCConfig.from_file(self.config_path)
        self.scene_map = config.routing
        self.controls = dict(config.controls)

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

        success = self._pulse(control.osc_path)
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
        success = self._pulse(control.osc_path)
        if success:
            self.active_scene = None
        return success

    def set_parameter(self, name: str, value: float) -> bool:
        control = self.controls.get(name)
        if control is None:
            logger.warning("QLC+ continuous control '%s' is not configured", name)
            return False
        return self.client.set_level(control.osc_path, value)

    def blackout(self, enabled: bool = True) -> bool:
        return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.client.close()


class QLCWebSocketBackend(LightingBackend):
    """QLC+ 5 Virtual Console backend resolved by normalized caption."""

    name = OUTPUT_QLC_WEBSOCKET

    def __init__(self, client: QLCWebSocketClient, scene_map: SceneMap, controls=None,
                 config_path: str | Path | None = None, scene_metadata=None):
        self.client = client
        self.scene_map = scene_map
        self.controls = dict(controls or {})
        self.config_path = Path(config_path) if config_path is not None else None
        self.scene_metadata = dict(scene_metadata or {})
        self.active_scene = None
        self._closed = False
        self._last_activation_error = None
        self._last_parameter_errors = {}

    def initialize(self):
        try:
            self.client.connect()
        except Exception:
            self.client.close()
            raise
        return True

    def resolve_scene(self, scene_name: str) -> str | None:
        return self.scene_map.resolve(scene_name)

    def activate_scene(self, scene_name: str) -> bool:
        requested_scene = scene_name
        scene_name = self.resolve_scene(scene_name)
        control = self.scene_map.get(scene_name) if scene_name is not None else None
        if control is None:
            message = f"QLC+ scene '{requested_scene}' has no WebSocket mapping"
            if self.scene_map.unmapped == "error":
                raise KeyError(message)
            logger.warning(message)
            return False
        if requested_scene != scene_name:
            logger.info("QLC+ scene request '%s' resolved to fallback '%s'", requested_scene, scene_name)
        if scene_name == self.active_scene:
            return True
        try:
            success = self.client.activate_button(control.caption)
        except QLCWebSocketError as exc:
            error = str(exc)
            if error != self._last_activation_error:
                logger.error("QLC+ WebSocket scene activation failed: %s", error)
                self._last_activation_error = error
            return False
        if success:
            self._last_activation_error = None
            self.active_scene = scene_name
        return success

    def deactivate_scene(self, scene_name: str) -> bool:
        # Activation-only contract: exclusivity belongs to a QLC+ Solo Frame.
        return True

    def set_parameter(self, name: str, value: float) -> bool:
        control = self.controls.get(name)
        if control is None:
            logger.warning("QLC+ continuous control '%s' is not configured", name)
            return False
        try:
            success = self.client.set_slider_level(control.caption, value)
        except QLCWebSocketError as exc:
            error = str(exc)
            if self._last_parameter_errors.get(name) != error:
                logger.error("QLC+ WebSocket continuous control '%s' failed: %s", name, error)
                self._last_parameter_errors[name] = error
            return False
        if success:
            self._last_parameter_errors.pop(name, None)
        return success

    def blackout(self, enabled: bool = True) -> bool:
        return False

    def reload_scene_map(self):
        if self.config_path is None:
            return
        config = QLCConfig.from_file(self.config_path)
        self.client.rediscover()
        self.scene_map = config.routing
        self.controls = dict(config.controls)
        self.scene_metadata = dict(config.scene_metadata)
        self.active_scene = None
        self._last_activation_error = None
        self._last_parameter_errors.clear()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.client.close()


class QLCNativeBackend(QLCWebSocketBackend):
    """Native QLC+ transport preserving caption-based backend semantics."""

    name = OUTPUT_QLC_NATIVE

    def initialize(self):
        self.client.start()
        return True

    def reload_scene_map(self):
        if self.config_path is None:
            return
        config = QLCConfig.from_file(self.config_path)
        self.scene_map = config.routing
        self.controls = dict(config.controls)
        self.scene_metadata = dict(config.scene_metadata)
        self.active_scene = None


def create_qlc_osc_backend(
    config_path: str | Path,
    *,
    host: str | None = None,
    port: int | None = None,
    dry_run: bool | None = None,
    log_filter_paths=(),
) -> QLCOscBackend:
    """Create a QLC+ backend with optional command-line overrides."""
    qlc_config = QLCConfig.from_file(config_path)
    config = qlc_config.transport
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
    backend = QLCOscBackend(
        OscClient(config, log_filter_paths=log_filter_paths),
        qlc_config.routing,
        controls=qlc_config.controls,
        config_path=config_path,
    )
    backend.initialize()
    return backend


def create_qlc_websocket_backend(
    config_path: str | Path,
    *, host: str | None = None, port: int | None = None,
    dry_run: bool | None = None, websocket_factory=None, inventory_loader=None,
) -> QLCWebSocketBackend:
    qlc_config = QLCConfig.from_file(config_path)
    config = qlc_config.websocket
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
    backend = QLCWebSocketBackend(
        QLCWebSocketClient(
            config,
            websocket_factory=websocket_factory,
            inventory_loader=inventory_loader,
        ),
        qlc_config.routing,
        controls=qlc_config.controls,
        config_path=config_path,
    )
    backend.initialize()
    return backend


def create_qlc_native_backend(
    config_path: str | Path, *, host: str | None = None, port: int | None = None,
    dry_run: bool | None = None, encryption_key: str | None = None,
) -> QLCNativeBackend:
    qlc_config = QLCConfig.from_file(config_path)
    config = qlc_config.native
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
        config = replace(config, **overrides)
        config.validate()
    backend = QLCNativeBackend(
        QLCNativeClient(
            config.host, config.port, config.encryption_key,
            config.reconnect_seconds, config.maximum_project_size, config.dry_run,
            button_release_seconds=qlc_config.routing.pulse_seconds,
        ),
        qlc_config.routing,
        controls=qlc_config.controls,
        config_path=config_path,
        scene_metadata=qlc_config.scene_metadata,
    )
    backend.initialize()
    return backend
