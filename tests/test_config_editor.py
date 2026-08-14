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
        self.assertEqual(interval.public()["section"], "Scene prediction")
        self.assertEqual(
            CONFIG_FIELD_BY_PATH["audio.frequency_modulation.bands.bass.low_hz"].public()["section"],
            "Bass control",
        )

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

    def test_service_overlay_reads_and_writes_effective_fields_to_deployment(self):
        deployment = Path(self.temp.name) / "deployment.json"
        deployment.write_text(json.dumps({
            "repository": "/opt/OculizerQLC",
            "audio_input": "USB Capture",
            "web_enabled": True,
            "web_bind": "0.0.0.0",
            "web_port": 8080,
        }), encoding="utf-8")
        store = ConfigurationStore(self.path, deployment)
        before = store.read()
        self.assertEqual(before["values"]["audio.input_device"], "USB Capture")
        self.assertEqual(before["sources"]["deployment"], str(deployment.resolve()))
        schema = {field["path"]: field for field in store.schema()}
        self.assertEqual(schema["audio.input_device"]["source"], "deployment")
        self.assertEqual(schema["audio.input_device"]["section"], "Service startup")
        self.assertEqual(schema["audio.silence.threshold"]["source"], "application")

        result = store.apply({
            "audio.input_device": "2",
            "audio.silence.duration_seconds": 1.25,
        }, before["revision"])

        self.assertEqual(json.loads(deployment.read_text())["audio_input"], "2")
        self.assertEqual(
            json.loads(self.path.read_text())["audio"]["silence"]["duration_seconds"],
            1.25,
        )
        self.assertEqual(json.loads(Path(str(deployment) + ".previous").read_text())["audio_input"],
                         "USB Capture")
        self.assertIn("audio.input_device", result["restart_required"])
        self.assertIn("audio.silence.duration_seconds", result["hot_applied"])

    def test_service_revision_includes_external_deployment_edits(self):
        deployment = Path(self.temp.name) / "deployment.json"
        deployment.write_text(json.dumps({"audio_input": "default"}), encoding="utf-8")
        store = ConfigurationStore(self.path, deployment)
        revision = store.read()["revision"]
        deployment.write_text(json.dumps({"audio_input": "changed"}), encoding="utf-8")

        with self.assertRaises(ConfigurationConflictError):
            store.apply({"audio.silence.duration_seconds": 1.0}, revision)


if __name__ == "__main__":
    unittest.main()
