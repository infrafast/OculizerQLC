import unittest
from unittest.mock import Mock

from oculizer.automatic import AutomaticSceneRouter, PolicyConflictError
from oculizer.runtime_control import RuntimeControl


class Backend:
    def __init__(self):
        self.blackout_active = False
        self.runtime_statuses = []

    def blackout(self, enabled=True):
        self.blackout_active = enabled
        return True

    def set_runtime_status(self, mode, dynamic_control):
        self.runtime_statuses.append((mode, dynamic_control))
        return True


class Engine:
    def __init__(self):
        self.scene_cache_size = 25
        self.current_predicted_scene = "wave"
        self.scene_manager = Mock(current_scene={"name": "silent"})
        self.backend = Backend()
        self.alive = True
        self.suspended = False

    def set_scene_cache_size(self, size):
        self.scene_cache_size = size

    def resolve_scene_target(self, scene):
        return scene if scene in {"wave", "silent", "party"} else None

    def change_scene(self, scene):
        target = self.resolve_scene_target(scene)
        if target is None:
            return False
        self.scene_manager.current_scene = {"name": target}
        return True

    def set_prediction_suspended(self, suspended):
        self.suspended = suspended

    def is_alive(self):
        return self.alive


def make_control():
    engine = Engine()
    router = AutomaticSceneRouter(engine)
    master = Mock()
    frequency = Mock()
    dynamic_controls = {
        "normal": {"cache": 7, "rate": (6, 10.0), "throttle": (3, 2.0)},
    }
    return RuntimeControl(
        engine, router, master, frequency, dynamic_controls=dynamic_controls,
        off_cache_size=25,
    ), engine, master, frequency


class ConfigStore:
    def __init__(self):
        self.callback = None

    def schema(self):
        return [{"path": "audio.silence.duration_seconds"}]

    def read(self):
        return {"revision": "abc", "values": {}}

    def apply(self, changes, expected_revision, live_apply):
        self.callback = live_apply
        config = {
            "audio": {
                "silence": {"duration_seconds": changes["audio.silence.duration_seconds"]},
            }
        }
        live_apply(config, {"audio.silence.duration_seconds"})
        return {"revision": "def", "hot_applied": list(changes), "restart_required": []}


class RuntimeControlTests(unittest.TestCase):
    def test_operator_status_is_change_only_and_translates_manual_mode(self):
        control, engine, _master, _frequency = make_control()
        self.assertEqual(engine.backend.runtime_statuses, [("auto", "off")])

        control.set_auto()
        control.set_scene("party")
        control.set_scene("silent")
        control.apply_dynamic_control("normal")
        control.set_pause()

        self.assertEqual(engine.backend.runtime_statuses, [
            ("auto", "off"),
            ("selection", "off"),
            ("selection", "normal"),
            ("pause", "normal"),
        ])

    def test_pause_only_suspends_prediction_and_runtime_updates(self):
        control, engine, master, frequency = make_control()

        status = control.set_pause()
        self.assertEqual(status["mode"], "pause")
        self.assertTrue(engine.suspended)
        self.assertFalse(engine.backend.blackout_active)
        master.shutdown.assert_not_called()
        frequency.shutdown.assert_not_called()
        self.assertFalse(control.tick())
        master.update.assert_not_called()

        control.set_auto()
        self.assertFalse(engine.suspended)
        master.startup.assert_not_called()
        frequency.startup.assert_not_called()

    def test_auto_and_scene_commands_share_router_state(self):
        control, engine, _master, _frequency = make_control()

        scene = control.set_scene("party")
        self.assertEqual(scene["mode"], "scene")
        self.assertEqual(scene["manual_scene"], "party")
        automatic = control.set_auto()
        self.assertEqual(automatic["mode"], "auto")
        self.assertIsNone(automatic["manual_scene"])

    def test_named_dynamic_control_applies_all_values_atomically(self):
        control, _engine, _master, _frequency = make_control()

        result = control.apply_dynamic_control("normal")

        self.assertEqual(result["dynamic_control"], "normal")
        self.assertEqual(result["scene_cache_size"], 7)
        self.assertEqual(result["scene_rate_limit"], (6, 10.0))
        self.assertEqual(result["scene_throttle"], (3, 2.0))

    def test_policy_revision_rejects_stale_editor(self):
        control, _engine, _master, _frequency = make_control()
        revision = control.status()["policy_revision"]
        control.apply_dynamic_control("normal")

        with self.assertRaises(PolicyConflictError):
            control.apply_dynamic_control("off", expected_revision=revision)

        self.assertEqual(control.status()["scene_cache_size"], 7)

    def test_off_restores_startup_cache_and_disables_transition_filters(self):
        control, _engine, _master, _frequency = make_control()
        control.apply_dynamic_control("normal")

        result = control.apply_dynamic_control("off")

        self.assertEqual(result["dynamic_control"], "off")
        self.assertEqual(result["scene_cache_size"], 25)
        self.assertIsNone(result["scene_rate_limit"])
        self.assertIsNone(result["scene_throttle"])

    def test_status_exposes_bounded_telemetry_without_changing_legacy_fields(self):
        control, engine, _master, _frequency = make_control()
        engine.current_audio_rms = 0.125
        engine.latest_prediction = "wave"
        engine.max_queue_depth_seen = 7

        result = control.handle({"command": "telemetry"})

        self.assertEqual(result["mode"], "auto")
        self.assertEqual(result["audio_rms"], 0.125)
        self.assertEqual(result["latest_prediction"], "wave")
        self.assertEqual(result["prediction_queue_max_seen"], 7)

    def test_configuration_commands_use_store_and_hot_apply_router_policy(self):
        control, _engine, _master, _frequency = make_control()
        store = ConfigStore()
        control.config_store = store

        self.assertEqual(control.handle({"command": "config-schema"})["fields"][0]["path"],
                         "audio.silence.duration_seconds")
        self.assertEqual(control.handle({"command": "config-get"})["revision"], "abc")
        result = control.handle({
            "command": "config-apply",
            "expected_revision": "abc",
            "changes": {"audio.silence.duration_seconds": 1.25},
        })
        self.assertEqual(result["revision"], "def")
        self.assertEqual(control.router.silence_config.duration_seconds, 1.25)

    def test_logs_are_bounded_and_validate_limit(self):
        control, _engine, _master, _frequency = make_control()
        control.log_provider = lambda limit: ["line"] * limit
        self.assertEqual(len(control.handle({"command": "logs", "limit": 3})["records"]), 3)
        with self.assertRaisesRegex(ValueError, "between 1 and 100"):
            control.handle({"command": "logs", "limit": 101})
