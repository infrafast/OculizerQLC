"""Shared operator control state for interactive and headless runtimes."""

from __future__ import annotations

import threading

from oculizer.runtime_config import (
    configured_dynamic_controls,
    configured_frequency_modulation,
    configured_master_modulation,
    configured_scene_max_duration,
    configured_silence,
    configured_speech,
)


class RuntimeControl:
    def __init__(self, oculizer, router, master_modulator, frequency_modulator,
                 dynamic_controls=None, active_dynamic_control="off", off_cache_size=10,
                 health_check=None, config_store=None, log_provider=None, launch_info=None):
        self.oculizer = oculizer
        self.router = router
        self.master_modulator = master_modulator
        self.frequency_modulator = frequency_modulator
        self.dynamic_controls = dynamic_controls or {}
        self.active_dynamic_control = active_dynamic_control
        self.off_cache_size = off_cache_size
        self.health_check = health_check or (lambda: bool(self.oculizer.is_alive()))
        self.config_store = config_store
        self.log_provider = log_provider
        self.launch_info = dict(launch_info or {})
        self.restart_callback = None
        self.mode = "auto"
        self.lock = threading.RLock()
        self._published_operator_state = None
        self._publish_operator_status()

    def _publish_operator_status(self):
        public_mode = "selection" if self.mode == "scene" else self.mode
        state = (public_mode, self.active_dynamic_control)
        if state == self._published_operator_state:
            return
        backend = getattr(self.oculizer, "backend", None)
        publisher = getattr(backend, "set_runtime_status", None)
        if publisher is not None:
            publisher(*state)
        self._published_operator_state = state

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
            self.mode = "auto"
            self.oculizer.set_prediction_suspended(False)
            if self.router.manual_override is not None:
                self.router.clear_manual_override()
            self._publish_operator_status()
            return self.status()

    def set_pause(self):
        with self.lock:
            self.mode = "pause"
            self.oculizer.set_prediction_suspended(True)
            self._publish_operator_status()
            return self.status()

    def set_scene(self, scene_name):
        with self.lock:
            if not isinstance(scene_name, str) or not scene_name.strip():
                raise ValueError("scene requires a non-empty logical scene name")
            if self.mode == "pause":
                self.oculizer.set_prediction_suspended(False)
            if not self.router.set_manual_override(scene_name):
                raise ValueError(f"unknown or unavailable scene: {scene_name}")
            self.mode = "scene"
            self._publish_operator_status()
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
            self._publish_operator_status()
            result = self.status()
            return result

    def status(self):
        with self.lock:
            policy = self.router.get_transition_policy_status()
            route = self.router.get_route_status()
            backend = getattr(self.oculizer, "backend", None)
            native_state = getattr(getattr(backend, "client", None), "state", None)
            current = getattr(getattr(self.oculizer, "scene_manager", None), "current_scene", None)
            prediction_queue = getattr(self.oculizer, "prediction_queue", None)
            try:
                queue_depth = prediction_queue.qsize()
            except (AttributeError, NotImplementedError):
                queue_depth = None
            return {
                "mode": self.mode,
                "manual_scene": self.router.manual_override,
                "resolved_scene": current.get("name") if isinstance(current, dict) else None,
                "blackout": bool(getattr(backend, "blackout_active", False)),
                "audio_worker_healthy": bool(self.health_check()),
                "lighting_state": getattr(native_state, "value", "ready"),
                "dynamic_control": self.active_dynamic_control,
                "predicted_scene": getattr(self.oculizer, "current_predicted_scene", None),
                "latest_prediction": getattr(self.oculizer, "latest_prediction", None),
                "audio_rms": getattr(self.oculizer, "current_audio_rms", None),
                "prediction_queue_depth": queue_depth,
                "prediction_queue_max_seen": getattr(self.oculizer, "max_queue_depth_seen", None),
                "launch": dict(self.launch_info),
                **route,
                **policy,
            }

    def _apply_runtime_configuration(self, config, changed_paths):
        """Replace hot-safe immutable policies while holding the runtime lock."""
        with self.lock:
            old = (
                self.router.silence_config,
                self.router.speech_config,
                self.master_modulator.config,
                self.frequency_modulator.config,
                self.dynamic_controls,
            )
            try:
                if any(path.startswith("audio.silence.") for path in changed_paths):
                    self.router.silence_config = configured_silence(config)
                    self.router.silence_started_at = None
                if any(path.startswith("audio.speech.") for path in changed_paths):
                    self.router.speech_config = configured_speech(config)
                    self.router.speech_started_at = None
                    self.router.speech_release_at = None
                if any(path.startswith("audio.master_modulation.") for path in changed_paths):
                    self.master_modulator.config = configured_master_modulation(config)
                    self.master_modulator.last_update_at = None
                    self.master_modulator.smoothed_value = None
                if any(path.startswith("audio.frequency_modulation.") for path in changed_paths):
                    self.frequency_modulator.config = configured_frequency_modulation(config)
                    self.frequency_modulator.last_update_at = None
                    self.frequency_modulator.smoothed_values.clear()
                    self.frequency_modulator.baselines.clear()
                if any(path.startswith("control.dynamic_controls.") for path in changed_paths):
                    self.dynamic_controls = configured_dynamic_controls(config)
                if "control.scene_max_duration_seconds" in changed_paths:
                    self.router.scene_max_duration = configured_scene_max_duration(config)
                    self.router.target_duration_seconds = None
            except Exception:
                (self.router.silence_config, self.router.speech_config,
                 self.master_modulator.config, self.frequency_modulator.config,
                 self.dynamic_controls) = old
                raise

    def configuration(self):
        if self.config_store is None:
            raise RuntimeError("configuration editing is unavailable")
        return self.config_store.read()

    def apply_configuration(self, changes, expected_revision):
        if self.config_store is None:
            raise RuntimeError("configuration editing is unavailable")
        return self.config_store.apply(
            changes,
            expected_revision,
            live_apply=self._apply_runtime_configuration,
        )

    def handle(self, request):
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        command = request.get("command")
        if command == "status":
            return self.status()
        if command == "telemetry":
            return self.status()
        if command == "logs":
            if self.log_provider is None:
                return {"records": []}
            limit = request.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
                raise ValueError("log limit must be between 1 and 100")
            return {"records": self.log_provider(limit)}
        if command == "config-schema":
            if self.config_store is None:
                raise RuntimeError("configuration editing is unavailable")
            return {"fields": self.config_store.schema()}
        if command == "config-get":
            return self.configuration()
        if command == "audio-devices":
            from oculizer.audio.sources import list_audio_input_devices
            return {"devices": list_audio_input_devices()}
        if command == "config-apply":
            return self.apply_configuration(
                request.get("changes"), request.get("expected_revision")
            )
        if command == "restart":
            if self.restart_callback is None:
                raise RuntimeError("automatic restart is unavailable for this runtime")
            result = {"accepted": True, "launch": dict(self.launch_info)}
            self.restart_callback()
            return result
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
