import threading
import time
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock

from oculizer.automatic import AutomaticSceneRouter
from oculizer.control_socket import send_control_request
from oculizer.headless import HeadlessOculizerService
from oculizer_service import configure_service_streams
from oculizer.runtime_config import SilenceConfig, SpeechConfig


class FakeOculizer:
    def __init__(self):
        self.current_predicted_scene = None
        self.targets = {"wave": "party", "party": "party", "silent": "silent"}
        self.changes = []
        self.alive = False
        self.current_audio_rms = None
        self.current_audioset_scores = None
        self.current_fast_audioset_scores = None
        self.latest_prediction = None
        self.prediction_suspended = False
        self.scene_cache_size = 25
        self.scene_cache = deque(maxlen=25)
        self.scene_durations = {}

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

    def set_scene_cache_size(self, size):
        if not 1 <= size <= 100:
            raise ValueError
        self.scene_cache_size = size

    def get_scene_max_duration(self, scene):
        return self.scene_durations.get(scene)


class AutomaticSceneRouterTests(unittest.TestCase):
    def test_expired_scene_uses_recent_distinct_prediction(self):
        engine = FakeOculizer()
        engine.targets.update({"strobe": "strobe", "blue": "blue", "ambient1": "ambient1"})
        engine.scene_cache.extend(["blue", "strobe"])
        engine.current_predicted_scene = "strobe"
        now = [0.0]
        router = AutomaticSceneRouter(
            engine, scene_max_duration=5, clock=lambda: now[0], random_source=lambda: 0.5
        )

        self.assertTrue(router.step())
        now[0] = 5.0
        self.assertTrue(router.step())

        self.assertEqual(engine.changes, ["strobe", "blue"])

    def test_expired_scene_uses_ambient_fallback_and_blocks_immediate_reentry(self):
        engine = FakeOculizer()
        engine.targets.update({"strobe": "strobe", "ambient1": "ambient1"})
        engine.current_predicted_scene = "strobe"
        now = [0.0]
        router = AutomaticSceneRouter(
            engine,
            scene_max_duration=5,
            scene_reentry_seconds=10,
            clock=lambda: now[0],
            random_source=lambda: 0.5,
        )

        self.assertTrue(router.step())
        now[0] = 5.0
        self.assertTrue(router.step())
        now[0] = 14.9
        self.assertFalse(router.step())
        now[0] = 15.0
        self.assertTrue(router.step())

        self.assertEqual(engine.changes, ["strobe", "ambient1", "strobe"])

    def test_scene_file_duration_overrides_global_default(self):
        engine = FakeOculizer()
        engine.targets.update({"strobe": "strobe", "ambient1": "ambient1"})
        engine.scene_durations["strobe"] = 2.0
        engine.current_predicted_scene = "strobe"
        now = [0.0]
        router = AutomaticSceneRouter(
            engine, scene_max_duration=30, clock=lambda: now[0], random_source=lambda: 0.5
        )

        self.assertTrue(router.step())
        now[0] = 1.9
        self.assertFalse(router.step())
        now[0] = 2.0
        self.assertTrue(router.step())

        self.assertEqual(engine.changes, ["strobe", "ambient1"])

    def test_expired_scene_bypasses_depleted_transition_limits(self):
        engine = FakeOculizer()
        engine.targets.update({
            "strobe": "strobe", "blue": "blue", "green": "green", "ambient1": "ambient1",
        })
        engine.scene_cache.extend(["blue", "strobe"])
        engine.current_predicted_scene = "strobe"
        now = [0.0]
        router = AutomaticSceneRouter(
            engine,
            scene_max_duration=5,
            scene_rate_limit=(1, 60.0),
            scene_throttle=(1, 60.0),
            clock=lambda: now[0],
            random_source=lambda: 0.5,
        )

        self.assertTrue(router.step())
        self.assertEqual(router.throttle_tokens, 0.0)
        now[0] = 5.0
        self.assertTrue(router.step())

        self.assertEqual(engine.changes, ["strobe", "blue"])
        self.assertEqual(list(router.automatic_change_times), [0.0, 5.0])
        self.assertEqual(router.throttle_tokens, 0.0)

        # The expired target is still predicted: retain the one selected
        # replacement instead of cycling through cached alternatives.
        now[0] = 5.1
        self.assertFalse(router.step())
        self.assertEqual(engine.changes, ["strobe", "blue"])

        # A genuinely different prediction returns to the ordinary policy and
        # cannot exploit the safety bypass for an immediate third transition.
        engine.current_predicted_scene = "green"
        self.assertFalse(router.step())
        self.assertEqual(engine.changes, ["strobe", "blue"])

    def test_duration_jitter_applies_to_override_and_global_default(self):
        engine = FakeOculizer()
        engine.targets.update({"strobe": "strobe", "party": "party"})
        engine.scene_durations["strobe"] = 8.0

        low = AutomaticSceneRouter(engine, scene_max_duration=40, random_source=lambda: 0.0)
        high = AutomaticSceneRouter(engine, scene_max_duration=40, random_source=lambda: 1.0)

        self.assertAlmostEqual(low._randomized_duration_for("strobe"), 5.6)
        self.assertAlmostEqual(high._randomized_duration_for("strobe"), 10.4)
        self.assertAlmostEqual(low._randomized_duration_for("party"), 28.0)
        self.assertAlmostEqual(high._randomized_duration_for("party"), 52.0)

    def test_duration_is_drawn_once_per_automatic_activation(self):
        engine = FakeOculizer()
        engine.targets.update({"strobe": "strobe", "ambient1": "ambient1"})
        engine.current_predicted_scene = "strobe"
        now = [0.0]
        samples = iter((0.0, 1.0))
        router = AutomaticSceneRouter(
            engine,
            scene_max_duration=10,
            clock=lambda: now[0],
            random_source=lambda: next(samples),
        )

        self.assertTrue(router.step())
        self.assertAlmostEqual(router.target_duration_seconds, 7.0)
        now[0] = 6.99
        self.assertFalse(router.step())
        self.assertAlmostEqual(router.target_duration_seconds, 7.0)
        now[0] = 7.0
        self.assertTrue(router.step())
        self.assertAlmostEqual(router.target_duration_seconds, 13.0)

    def test_live_controls_apply_atomically_and_reset_runtime_budgets(self):
        engine = FakeOculizer()
        engine.scene_cache_size = 25
        now = [0.0]
        router = AutomaticSceneRouter(
            engine,
            scene_rate_limit=(2, 5.0),
            scene_throttle=(2, 2.0),
            clock=lambda: now[0],
        )
        engine.current_predicted_scene = "party"
        self.assertTrue(router.step())

        status = router.configure_transition_policy(
            scene_cache_size=5,
            scene_rate_limit=(6, 10.0),
            scene_throttle=(3, 2.0),
        )

        self.assertEqual(status["scene_cache_size"], 5)
        self.assertEqual(status["scene_rate_used"], 0)
        self.assertEqual(status["throttle_tokens"], 3.0)

    def test_invalid_live_controls_preserve_all_previous_values(self):
        engine = FakeOculizer()
        engine.scene_cache_size = 25
        router = AutomaticSceneRouter(
            engine,
            scene_rate_limit=(4, 5.0),
            scene_throttle=(3, 2.0),
        )

        with self.assertRaises(ValueError):
            router.configure_transition_policy(
                scene_cache_size=4,
                scene_rate_limit=(1, 0),
                scene_throttle=(2, 1.0),
            )

        status = router.get_transition_policy_status()
        self.assertEqual(status["scene_cache_size"], 25)
        self.assertEqual(status["scene_rate_limit"], (4, 5.0))
        self.assertEqual(status["scene_throttle"], (3, 2.0))

    def test_scene_throttle_allows_burst_then_applies_latest_after_recovery(self):
        engine = FakeOculizer()
        engine.targets.update({name: name for name in ("one", "two", "three", "held", "latest")})
        now = [0.0]
        router = AutomaticSceneRouter(engine, scene_throttle=(3, 2.0), clock=lambda: now[0])

        for scene in ("one", "two", "three"):
            engine.current_predicted_scene = scene
            self.assertTrue(router.step())
        engine.current_predicted_scene = "held"
        self.assertFalse(router.step())
        now[0] = 1.9
        self.assertFalse(router.step())
        now[0] = 2.0
        engine.current_predicted_scene = "latest"
        self.assertTrue(router.step())

        self.assertEqual(engine.changes, ["one", "two", "three", "latest"])
        self.assertEqual(len(router.automatic_change_times), 0)

    def test_rate_limit_can_cap_throttle_initial_burst(self):
        engine = FakeOculizer()
        engine.targets.update({name: name for name in ("one", "two", "three")})
        router = AutomaticSceneRouter(
            engine,
            scene_rate_limit=(2, 10.0),
            scene_throttle=(3, 2.0),
            clock=lambda: 0.0,
        )

        for scene in ("one", "two"):
            engine.current_predicted_scene = scene
            self.assertTrue(router.step())
        engine.current_predicted_scene = "three"
        self.assertFalse(router.step())

        self.assertEqual(engine.changes, ["one", "two"])
        self.assertEqual(router.throttle_tokens, 1.0)

    def test_scene_rate_limit_uses_a_rolling_window(self):
        engine = FakeOculizer()
        engine.targets.update({name: name for name in ("one", "two", "three", "four")})
        now = [0.0]
        router = AutomaticSceneRouter(engine, scene_rate_limit=(2, 5.0), clock=lambda: now[0])

        engine.current_predicted_scene = "one"
        self.assertTrue(router.step())
        now[0] = 1.0
        engine.current_predicted_scene = "two"
        self.assertTrue(router.step())
        now[0] = 4.9
        engine.current_predicted_scene = "three"
        self.assertFalse(router.step())
        now[0] = 5.0
        engine.current_predicted_scene = "four"
        self.assertTrue(router.step())

        self.assertEqual(engine.changes, ["one", "two", "four"])

    def test_silence_and_resume_bypass_transition_limits(self):
        engine = FakeOculizer()
        engine.current_predicted_scene = "wave"
        now = [0.0]
        router = AutomaticSceneRouter(
            engine,
            silence_config=SilenceConfig(duration_seconds=0, scene="silent"),
            scene_rate_limit=(1, 60.0),
            scene_throttle=(1, 60.0),
            clock=lambda: now[0],
        )

        engine.current_audio_rms = 0.1
        self.assertTrue(router.step())
        now[0] = 1.0
        engine.current_audio_rms = 0.0
        self.assertTrue(router.step())
        now[0] = 2.0
        engine.current_audio_rms = 0.1
        self.assertTrue(router.step())

        self.assertEqual(engine.changes, ["wave", "silent", "wave"])

    def test_manual_override_bypasses_transition_limits(self):
        engine = FakeOculizer()
        engine.current_predicted_scene = "party"
        router = AutomaticSceneRouter(
            engine,
            scene_rate_limit=(1, 60.0),
            scene_throttle=(1, 60.0),
        )

        self.assertTrue(router.step())
        self.assertTrue(router.set_manual_override("silent"))

        self.assertEqual(engine.changes, ["party", "silent"])

    def test_clearing_manual_override_resumes_immediately_despite_limits(self):
        engine = FakeOculizer()
        engine.current_predicted_scene = "party"
        router = AutomaticSceneRouter(engine, scene_throttle=(1, 60.0))

        self.assertTrue(router.step())
        self.assertTrue(router.set_manual_override("silent"))
        self.assertTrue(router.clear_manual_override())

        self.assertEqual(engine.changes, ["party", "silent", "party"])

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

        self.assertTrue(router.set_manual_override("silent"))
        self.assertFalse(router.step())
        self.assertTrue(router.clear_manual_override())

        self.assertEqual(engine.changes, ["silent", "party"])

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
            silence_config=SilenceConfig(duration_seconds=0, scene="silent"),
        )

        self.assertTrue(router.set_manual_override("party"))
        self.assertFalse(router.step())
        self.assertEqual(engine.changes, ["party"])

    def test_silence_clears_previous_speech_state(self):
        engine = FakeOculizer()
        engine.current_audio_rms = 0.0
        router = AutomaticSceneRouter(
            engine,
            silence_config=SilenceConfig(duration_seconds=0, scene="silent"),
            speech_config=SpeechConfig(minimum_duration_seconds=0),
        )
        router.speech_active = True
        router.speech_started_at = 1.0

        self.assertTrue(router.step())

        self.assertTrue(router.silence_active)
        self.assertFalse(router.speech_active)
        self.assertIsNone(router.speech_started_at)

    def test_dominant_speech_routes_announcement_then_releases(self):
        engine = FakeOculizer()
        engine.targets["announcement"] = "announcement"
        engine.current_predicted_scene = "wave"
        now = [0.0]
        router = AutomaticSceneRouter(engine, speech_config=SpeechConfig(minimum_duration_seconds=1, release_duration_seconds=1), clock=lambda: now[0])
        engine.current_audioset_scores = {"speech": 0.8, "music": 0.2, "singing": 0.0}
        self.assertFalse(router.step())  # Hold the current scene while speech is confirmed.
        now[0] = 1.0
        self.assertTrue(router.step())
        engine.current_audioset_scores = {"speech": 0.3, "music": 0.7, "singing": 0.7}
        now[0] = 2.0
        self.assertFalse(router.step())
        now[0] = 3.0
        self.assertTrue(router.step())
        self.assertEqual(engine.changes, ["announcement", "wave"])

    def test_ambiguous_scores_hold_announcement_instead_of_leaking_music_scenes(self):
        engine = FakeOculizer()
        engine.targets["announcement"] = "announcement"
        engine.current_predicted_scene = "wave"
        now = [0.0]
        router = AutomaticSceneRouter(
            engine,
            speech_config=SpeechConfig(minimum_duration_seconds=0, release_duration_seconds=1),
            clock=lambda: now[0],
        )

        engine.current_audioset_scores = {"speech": 0.8, "music": 0.01, "singing": 0.0}
        self.assertTrue(router.step())
        engine.current_audioset_scores = {"speech": 0.3, "music": 0.01, "singing": 0.0}
        now[0] = 5.0
        self.assertFalse(router.step())

        self.assertTrue(router.speech_active)
        self.assertEqual(engine.changes, ["announcement"])

    def test_music_prediction_waits_for_dominant_music_scores(self):
        engine = FakeOculizer()
        engine.current_predicted_scene = "wave"
        router = AutomaticSceneRouter(engine, speech_config=SpeechConfig())

        self.assertFalse(router.step())  # No semantic scores are available yet.
        engine.current_audioset_scores = {"speech": 0.2, "music": 0.2, "singing": 0.0}
        self.assertFalse(router.step())
        engine.current_audioset_scores = {"speech": 0.1, "music": 0.8, "singing": 0.0}
        self.assertTrue(router.step())

        self.assertEqual(engine.changes, ["wave"])

    def test_fast_speech_bypasses_every_dynamic_policy(self):
        for name, rate, throttle in (
            ("responsive", (1, 60.0), (1, 60.0)),
            ("normal", (1, 60.0), (1, 60.0)),
            ("calm", (1, 60.0), (1, 60.0)),
        ):
            with self.subTest(profile=name):
                engine = FakeOculizer()
                engine.targets.update({"announcement": "announcement"})
                engine.current_predicted_scene = "party"
                engine.current_fast_audioset_scores = {
                    "speech": 0.9, "music": 0.1, "singing": 0.0,
                }
                router = AutomaticSceneRouter(
                    engine,
                    speech_config=SpeechConfig(minimum_duration_seconds=0),
                    scene_rate_limit=rate,
                    scene_throttle=throttle,
                )
                router.automatic_change_times.append(router.clock())
                router.throttle_tokens = 0.0

                self.assertTrue(router.step())
                self.assertEqual(engine.changes, ["announcement"])
                self.assertEqual(router.get_route_status()["route_reason"], "priority_speech")

    def test_silence_bypasses_depleted_ordinary_limits(self):
        engine = FakeOculizer()
        engine.current_predicted_scene = "party"
        now = [0.0]
        router = AutomaticSceneRouter(
            engine,
            speech_config=SpeechConfig(enabled=False),
            silence_config=SilenceConfig(scene="silent", duration_seconds=0.1),
            scene_rate_limit=(1, 60.0),
            scene_throttle=(1, 60.0),
            clock=lambda: now[0],
        )
        engine.current_audio_rms = 0.1
        self.assertTrue(router.step())
        engine.current_audio_rms = 0.0
        now[0] = 1.0
        self.assertFalse(router.step())
        now[0] = 1.11
        self.assertTrue(router.step())

        self.assertEqual(engine.changes, ["party", "silent"])
        self.assertEqual(router.get_route_status()["route_reason"], "priority_silence")

    def test_status_distinguishes_stable_candidate_blocked_by_policy(self):
        engine = FakeOculizer()
        engine.targets.update({"one": "one", "two": "two"})
        router = AutomaticSceneRouter(engine, scene_rate_limit=(1, 60.0))
        engine.current_predicted_scene = "one"
        engine.latest_prediction = "one"
        self.assertTrue(router.step())
        engine.current_predicted_scene = "two"
        engine.latest_prediction = "two"
        self.assertFalse(router.step())

        status = router.get_route_status()
        self.assertEqual(status["active_scene"], "one")
        self.assertEqual(status["stable_candidate_scene"], "two")
        self.assertEqual(status["transition_block_reason"], "rate limit 1/60s")


class HeadlessServiceTests(unittest.TestCase):
    def test_headless_service_exposes_shared_control_socket(self):
        engine = FakeOculizer()
        engine.current_predicted_scene = "party"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.sock"
            service = HeadlessOculizerService(engine, poll_seconds=0.001, control_socket_path=path)
            result = []
            thread = threading.Thread(target=lambda: result.append(service.run()))
            thread.start()
            deadline = time.time() + 1.0
            while not path.exists() and time.time() < deadline:
                time.sleep(0.001)

            status = send_control_request(path, {"command": "status"})
            paused = send_control_request(path, {"command": "pause"})
            service.request_stop()
            thread.join(timeout=2.0)

        self.assertEqual(status["mode"], "auto")
        self.assertEqual(paused["mode"], "pause")
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [0])

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
