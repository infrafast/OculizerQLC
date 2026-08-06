"""Shared operator control state for interactive and headless runtimes."""

from __future__ import annotations

import threading


class RuntimeControl:
    def __init__(self, oculizer, router, master_modulator, frequency_modulator,
                 dynamic_controls=None, active_dynamic_control="off", off_cache_size=10,
                 health_check=None):
        self.oculizer = oculizer
        self.router = router
        self.master_modulator = master_modulator
        self.frequency_modulator = frequency_modulator
        self.dynamic_controls = dynamic_controls or {}
        self.active_dynamic_control = active_dynamic_control
        self.off_cache_size = off_cache_size
        self.health_check = health_check or (lambda: bool(self.oculizer.is_alive()))
        self.mode = "auto"
        self.lock = threading.RLock()

    def _blackout(self, enabled):
        backend = getattr(self.oculizer, "backend", None)
        return bool(backend and backend.blackout(enabled))

    def step(self):
        with self.lock:
            return False if self.mode == "pause" else self.router.step()

    def tick(self):
        """Run one routing/modulation cycle unless operator pause is active."""
        with self.lock:
            if self.mode == "pause":
                return False
            changed = self.router.step()
            self.master_modulator.update()
            self.frequency_modulator.update()
            return changed

    def set_auto(self):
        with self.lock:
            was_paused = self.mode == "pause"
            self.mode = "auto"
            self.oculizer.set_prediction_suspended(False)
            self._blackout(False)
            if was_paused:
                self.master_modulator.startup()
                self.frequency_modulator.startup()
            if self.router.manual_override is not None:
                self.router.clear_manual_override()
            return self.status()

    def set_pause(self):
        with self.lock:
            self.mode = "pause"
            self.oculizer.set_prediction_suspended(True)
            self.master_modulator.shutdown()
            self.frequency_modulator.shutdown()
            self._blackout(True)
            return self.status()

    def set_scene(self, scene_name):
        with self.lock:
            if not isinstance(scene_name, str) or not scene_name.strip():
                raise ValueError("scene requires a non-empty logical scene name")
            if self.mode == "pause":
                self.oculizer.set_prediction_suspended(False)
                self._blackout(False)
                self.master_modulator.startup()
                self.frequency_modulator.startup()
            if not self.router.set_manual_override(scene_name):
                raise ValueError(f"unknown or unavailable scene: {scene_name}")
            self.mode = "scene"
            return self.status()

    def apply_dynamic_control(self, name, expected_revision=None):
        with self.lock:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("dynamic-control requires a profile name")
            name = name.strip()
            if name == "off":
                profile = {"cache": self.off_cache_size, "rate": None, "throttle": None}
            elif name in self.dynamic_controls:
                profile = self.dynamic_controls[name]
            else:
                raise ValueError(f"unknown dynamic-control profile: {name}")
            self.router.configure_transition_policy(
                scene_cache_size=profile["cache"],
                scene_rate_limit=profile["rate"],
                scene_throttle=profile["throttle"],
                expected_revision=expected_revision,
            )
            self.active_dynamic_control = name
            result = self.status()
            return result

    def status(self):
        with self.lock:
            policy = self.router.get_transition_policy_status()
            backend = getattr(self.oculizer, "backend", None)
            current = getattr(getattr(self.oculizer, "scene_manager", None), "current_scene", None)
            return {
                "mode": self.mode,
                "manual_scene": self.router.manual_override,
                "resolved_scene": current.get("name") if isinstance(current, dict) else None,
                "blackout": bool(getattr(backend, "blackout_active", self.mode == "pause")),
                "audio_worker_healthy": bool(self.health_check()),
                "dynamic_control": self.active_dynamic_control,
                **policy,
            }

    def handle(self, request):
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        command = request.get("command")
        if command == "status":
            return self.status()
        if command == "auto":
            return self.set_auto()
        if command == "pause":
            return self.set_pause()
        if command == "scene":
            return self.set_scene(request.get("scene"))
        if command == "dynamic-controls":
            return {"active": self.active_dynamic_control,
                    "dynamic_controls": {"off": {"cache": self.off_cache_size,
                                                    "rate": None, "throttle": None},
                                         **self.dynamic_controls}}
        if command == "dynamic-control":
            return self.apply_dynamic_control(
                request.get("name"), expected_revision=request.get("expected_revision")
            )
        raise ValueError(f"unknown command: {command}")
