import unittest
from unittest.mock import Mock

from oculizer.automatic import AutomaticSceneRouter, PolicyConflictError
from oculizer.runtime_control import RuntimeControl


class Backend:
    def __init__(self):
        self.blackout_active = False

    def blackout(self, enabled=True):
        self.blackout_active = enabled
        return True


class Engine:
    def __init__(self):
        self.scene_cache_size = 25
        self.current_predicted_scene = "wave"
        self.scene_manager = Mock(current_scene={"name": "off"})
        self.backend = Backend()
        self.alive = True
        self.suspended = False

    def set_scene_cache_size(self, size):
        self.scene_cache_size = size

    def resolve_scene_target(self, scene):
        return scene if scene in {"wave", "off", "party"} else None

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
    presets = {
        "normal": {"cache": 7, "rate": (6, 10.0), "throttle": (3, 2.0)},
        "reset": {"cache": 25, "rate": None, "throttle": None},
    }
    return RuntimeControl(engine, router, master, frequency, presets=presets), engine, master, frequency


class RuntimeControlTests(unittest.TestCase):
    def test_pause_sets_safe_state_and_tick_stops_all_updates(self):
        control, engine, master, frequency = make_control()

        status = control.set_pause()
        self.assertEqual(status["mode"], "pause")
        self.assertTrue(engine.suspended)
        self.assertTrue(engine.backend.blackout_active)
        master.shutdown.assert_called_once_with()
        frequency.shutdown.assert_called_once_with()
        self.assertFalse(control.tick())
        master.update.assert_not_called()

    def test_auto_and_scene_commands_share_router_state(self):
        control, engine, _master, _frequency = make_control()

        scene = control.set_scene("party")
        self.assertEqual(scene["mode"], "scene")
        self.assertEqual(scene["manual_scene"], "party")
        automatic = control.set_auto()
        self.assertEqual(automatic["mode"], "auto")
        self.assertIsNone(automatic["manual_scene"])

    def test_named_preset_applies_all_values_atomically(self):
        control, _engine, _master, _frequency = make_control()

        result = control.apply_preset("normal")

        self.assertEqual(result["preset"], "normal")
        self.assertEqual(result["scene_cache_size"], 7)
        self.assertEqual(result["scene_rate_limit"], (6, 10.0))
        self.assertEqual(result["scene_throttle"], (3, 2.0))

    def test_policy_revision_rejects_stale_editor(self):
        control, _engine, _master, _frequency = make_control()
        revision = control.status()["policy_revision"]
        control.configure_limits(cache=5)

        with self.assertRaises(PolicyConflictError):
            control.configure_limits(cache=6, expected_revision=revision)

        self.assertEqual(control.status()["scene_cache_size"], 5)

