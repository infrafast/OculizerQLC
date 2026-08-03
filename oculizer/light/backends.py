"""Interchangeable lighting output backends for Oculizer."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from oculizer.light.osc_client import OscClient, OscConfig


logger = logging.getLogger(__name__)

OUTPUT_ENTTEC = "enttec"
OUTPUT_QLC_OSC = "qlc-osc"
OUTPUT_DISABLED = "disabled"
OUTPUT_CHOICES = (OUTPUT_ENTTEC, OUTPUT_QLC_OSC)


class LightingBackend(ABC):
    """Intent-level interface shared by all lighting outputs."""

    name: str
    supports_direct_fixture_output: bool = False

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

    def __init__(self, client: OscClient):
        self.client = client

    def activate_scene(self, scene_name: str) -> bool:
        logger.debug("QLC+ scene activation is deferred to phase 3: %s", scene_name)
        return False

    def deactivate_scene(self, scene_name: str) -> bool:
        logger.debug("QLC+ scene deactivation is deferred to phase 3: %s", scene_name)
        return False

    def set_parameter(self, name: str, value: float) -> bool:
        address = name if name.startswith("/") else f"/oculizer/{name}"
        return self.client.set_level(address, value)

    def blackout(self, enabled: bool = True) -> bool:
        return self.client.blackout(enabled)

    def close(self) -> None:
        self.client.close()


def create_qlc_osc_backend(
    config_path: str | Path,
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
    return QLCOscBackend(OscClient(config))
