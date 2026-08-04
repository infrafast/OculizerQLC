"""Shared operator control state for interactive and headless runtimes."""

from __future__ import annotations

import threading


class RuntimeControl:
    def __init__(self, oculizer, router, master_modulator, frequency_modulator,
                 presets=None, health_check=None):
        self.oculizer = oculizer
        self.router = router
        self.master_modulator = master_modulator
        self.frequency_modulator = frequency_modulator
        self.presets = presets or {}
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

    def configure_limits(self, *, cache="unchanged", rate="unchanged", throttle="unchanged",
                         expected_revision=None):
        if isinstance(rate, list):
            rate = tuple(rate)
        if isinstance(throttle, list):
            throttle = tuple(throttle)
        kwargs = {"expected_revision": expected_revision}
        if cache != "unchanged":
            kwargs["scene_cache_size"] = cache
        if rate != "unchanged":
            kwargs["scene_rate_limit"] = rate
        if throttle != "unchanged":
            kwargs["scene_throttle"] = throttle
        return self.router.configure_transition_policy(**kwargs)

    def apply_preset(self, name):
        with self.lock:
            if name not in self.presets:
                raise ValueError(f"unknown preset: {name}")
            preset = self.presets[name]
            self.router.configure_transition_policy(
                scene_cache_size=preset["cache"],
                scene_rate_limit=preset["rate"],
                scene_throttle=preset["throttle"],
            )
            result = self.status()
            result["preset"] = name
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
        if command == "limits":
            if not any(key in request for key in ("cache", "rate", "throttle")):
                return self.router.get_transition_policy_status()
            return self.configure_limits(
                cache=request.get("cache", "unchanged"),
                rate=request.get("rate", "unchanged"),
                throttle=request.get("throttle", "unchanged"),
                expected_revision=request.get("expected_revision"),
            )
        if command == "presets":
            return {"presets": self.presets}
        if command == "preset":
            return self.apply_preset(request.get("name"))
        raise ValueError(f"unknown command: {command}")
