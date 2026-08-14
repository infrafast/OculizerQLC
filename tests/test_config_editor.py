import json
from pathlib import Path
import tempfile
import unittest

from oculizer.config_editor import (
    CONFIG_FIELD_BY_PATH,
    ConfigurationConflictError,
    ConfigurationStore,
)


BASE_CONFIG = {
    "audio": {
        "input_device": "default",
        "prediction": {"window_seconds": 4.0, "interval_seconds": 1.0},
        "silence": {
            "enabled": True, "threshold": 0.001, "resume_threshold": 0.002,
            "duration_seconds": 2.0, "scene": "silent",
        },
        "speech": {
            "enabled": True, "threshold": 0.55, "music_margin": 0.15,
            "minimum_duration_seconds": 1.0, "release_duration_seconds": 0.75,
            "scene": "announcement",
        },
    }
}


class ConfigurationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "oculizer.json"
        self.path.write_text(json.dumps(BASE_CONFIG), encoding="utf-8")
        self.store = ConfigurationStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_schema_has_shared_limits_help_and_apply_mode(self):
        interval = CONFIG_FIELD_BY_PATH["audio.prediction.interval_seconds"]
        self.assertEqual(interval.minimum, 0.1)
        self.assertEqual(interval.recommended_minimum, 0.75)
        self.assertEqual(interval.apply_mode, "restart")
        self.assertIn("Raspberry Pi", interval.help)

    def test_atomic_apply_creates_backup_and_reports_hot_and_restart_fields(self):
        before = self.store.read()
        applied = []

        result = self.store.apply({
            "audio.input_device": "BlackHole",
            "audio.silence.duration_seconds": 1.5,
        }, before["revision"], live_apply=lambda config, paths: applied.append((config, paths)))

        self.assertEqual(result["restart_required"], ["audio.input_device"])
        self.assertEqual(result["hot_applied"], ["audio.silence.duration_seconds"])
        self.assertEqual(applied[0][1], {"audio.silence.duration_seconds"})
        self.assertEqual(json.loads(self.path.read_text())["audio"]["input_device"], "BlackHole")
        self.assertEqual(json.loads(self.store.backup_path.read_text())["audio"]["input_device"], "default")

    def test_stale_revision_and_unknown_field_change_nothing(self):
        revision = self.store.read()["revision"]
        self.path.write_text(json.dumps({**BASE_CONFIG, "external": True}), encoding="utf-8")
        raw = self.path.read_bytes()
        with self.assertRaises(ConfigurationConflictError):
            self.store.apply({"audio.silence.duration_seconds": 1.0}, revision)
        self.assertEqual(self.path.read_bytes(), raw)

        current = self.store.read()["revision"]
        with self.assertRaisesRegex(ValueError, "unknown or read-only"):
            self.store.apply({"lighting.native.encryption_key": "secret"}, current)
        self.assertEqual(self.path.read_bytes(), raw)

    def test_cross_field_validation_rolls_back_before_live_apply(self):
        before = self.store.read()
        callbacks = []
        with self.assertRaisesRegex(ValueError, "resume_threshold"):
            self.store.apply({
                "audio.silence.threshold": 0.5,
            }, before["revision"], live_apply=lambda *_: callbacks.append(True))
        self.assertEqual(callbacks, [])
        self.assertEqual(self.store.read()["revision"], before["revision"])

    def test_live_apply_failure_restores_file_and_runtime_callback(self):
        before = self.store.read()
        calls = []

        def apply(config, paths):
            calls.append(config["audio"]["silence"]["duration_seconds"])
            if len(calls) == 1:
                raise RuntimeError("runtime rejected update")

        with self.assertRaisesRegex(RuntimeError, "runtime rejected"):
            self.store.apply({"audio.silence.duration_seconds": 1.0}, before["revision"], apply)
        self.assertEqual(calls, [1.0, 2.0])
        self.assertEqual(self.store.read()["revision"], before["revision"])


if __name__ == "__main__":
    unittest.main()
