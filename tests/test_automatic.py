import threading
import time
import unittest
from unittest.mock import Mock

from oculizer.automatic import AutomaticSceneRouter
from oculizer.headless import HeadlessOculizerService
from oculizer_service import configure_service_streams
from oculizer.runtime_config import SilenceConfig


class FakeOculizer:
    def __init__(self):
        self.current_predicted_scene = None
        self.targets = {"wave": "party", "party": "party", "off": "off"}
        self.changes = []
        self.alive = False
        self.current_audio_rms = None
        self.prediction_suspended = False

    def resolve_scene_target(self, scene):
        return self.targets.get(scene)

    def change_scene(self, scene):
        if self.resolve_scene_target(scene) is None:
            return False
        self.changes.append(scene)
        return True

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def stop(self):
        self.alive = False

    def join(self, timeout=None):
        return None

    def set_prediction_suspended(self, suspended):
        self.prediction_suspended = suspended


class AutomaticSceneRouterTests(unittest.TestCase):
    def test_applies_prediction_fallback_and_deduplicates_resolved_target(self):
        engine = FakeOculizer()
        router = AutomaticSceneRouter(engine)

        engine.current_predicted_scene = "wave"
        self.assertTrue(router.step())
        engine.current_predicted_scene = "party"
        self.assertFalse(router.step())

        self.assertEqual(engine.changes, ["wave"])
        self.assertEqual(router.last_target, "party")

    def test_manual_override_blocks_prediction_until_cleared(self):
        engine = FakeOculizer()
        router = AutomaticSceneRouter(engine)
        engine.current_predicted_scene = "party"

        self.assertTrue(router.set_manual_override("off"))
        self.assertFalse(router.step())
        self.assertTrue(router.clear_manual_override())

        self.assertEqual(engine.changes, ["off", "party"])

    def test_rejects_unknown_prediction_without_changing_scene(self):
        engine = FakeOculizer()
        router = AutomaticSceneRouter(engine)
        engine.current_predicted_scene = "unknown"

        self.assertFalse(router.step())
        self.assertEqual(engine.changes, [])

    def test_routes_sustained_silence_to_configured_scene_and_resumes(self):
        engine = FakeOculizer()
        engine.current_predicted_scene = "wave"
        engine.targets["ambient"] = "ambient"
        now = [0.0]
        router = AutomaticSceneRouter(
            engine,
            silence_config=SilenceConfig(
                threshold=0.01,
                resume_threshold=0.02,
                duration_seconds=2.0,
                scene="ambient",
            ),
            clock=lambda: now[0],
        )

        engine.current_audio_rms = 0.005
        self.assertTrue(router.step())  # Prediction remains active during grace period.
        now[0] = 1.9
        self.assertFalse(router.step())
        now[0] = 2.0
        self.assertTrue(router.step())
        self.assertTrue(router.silence_active)
        self.assertTrue(engine.prediction_suspended)

        engine.current_audio_rms = 0.015  # Between thresholds: remain silent.
        self.assertFalse(router.step())
        engine.current_audio_rms = 0.021
        self.assertTrue(router.step())
        self.assertFalse(router.silence_active)
        self.assertFalse(engine.prediction_suspended)
        self.assertEqual(engine.changes, ["wave", "ambient", "wave"])

    def test_manual_override_has_priority_over_silence(self):
        engine = FakeOculizer()
        engine.current_audio_rms = 0.0
        router = AutomaticSceneRouter(
            engine,
            silence_config=SilenceConfig(duration_seconds=0, scene="off"),
        )

        self.assertTrue(router.set_manual_override("party"))
        self.assertFalse(router.step())
        self.assertEqual(engine.changes, ["party"])


class HeadlessServiceTests(unittest.TestCase):
    def test_configures_explicit_carriage_return_line_endings(self):
        stdout = Mock()
        stderr = Mock()
        with unittest.mock.patch("oculizer_service.sys.stdout", stdout), unittest.mock.patch(
            "oculizer_service.sys.stderr", stderr
        ):
            configure_service_streams()

        stdout.reconfigure.assert_called_once_with(newline="\r\n", line_buffering=True)
        stderr.reconfigure.assert_called_once_with(newline="\r\n", line_buffering=True)

    def test_runs_without_terminal_and_stops_cleanly(self):
        engine = FakeOculizer()
        engine.current_predicted_scene = "party"
        service = HeadlessOculizerService(engine, poll_seconds=0.001)
        result = []
        thread = threading.Thread(target=lambda: result.append(service.run()))

        thread.start()
        deadline = time.time() + 1.0
        while not engine.changes and time.time() < deadline:
            time.sleep(0.001)
        service.request_stop()
        thread.join(timeout=1.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [0])
        self.assertEqual(engine.changes, ["party"])
        self.assertFalse(engine.alive)

    def test_reports_unexpected_worker_exit(self):
        engine = Mock()
        engine.is_alive.return_value = False
        service = HeadlessOculizerService(engine, poll_seconds=0)

        self.assertEqual(service.run(), 1)
        engine.stop.assert_called_once_with()
        engine.join.assert_called_once_with(timeout=5.0)


if __name__ == "__main__":
    unittest.main()
