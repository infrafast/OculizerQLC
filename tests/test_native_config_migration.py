import json
import unittest
from pathlib import Path

from scripts.migrate_native_only_config import (
    DEFAULT_APPLICATION_CONFIG,
    DEFAULT_QLC_CONFIG,
    DEFAULT_SCENES,
    BEHAVIORS,
    build_lighting_config,
    classify_scene,
)


class NativeConfigMigrationTests(unittest.TestCase):
    def setUp(self):
        self.legacy = json.loads(DEFAULT_QLC_CONFIG.read_text(encoding="utf-8"))
        self.application = json.loads(DEFAULT_APPLICATION_CONFIG.read_text(encoding="utf-8"))
        self.lighting = build_lighting_config(self.legacy, DEFAULT_SCENES)

    def test_migrates_every_description_duration_and_native_setting_exactly(self):
        metadata = self.lighting["scene_metadata"]
        self.assertEqual(len(metadata), 127)
        self.assertEqual(self.lighting["native"], self.legacy["native"])
        duration_count = 0
        for path in DEFAULT_SCENES.glob("*.json"):
            legacy_scene = json.loads(path.read_text(encoding="utf-8"))
            migrated = metadata[path.stem]
            self.assertEqual(migrated["description"], legacy_scene["description"].strip())
            self.assertIn(migrated["design_behavior"], BEHAVIORS)
            if "max_duration_seconds" in legacy_scene:
                duration_count += 1
                self.assertEqual(
                    migrated["max_duration_seconds"],
                    legacy_scene["max_duration_seconds"],
                )
            else:
                self.assertNotIn("max_duration_seconds", migrated)
        self.assertEqual(duration_count, 23)

    def test_native_config_contains_no_legacy_transport_paths(self):
        serialized = json.dumps(self.lighting)
        for obsolete in ("OSCPath", "OSCaction", "websocket", '"transport"'):
            self.assertNotIn(obsolete, serialized)
        self.assertEqual(
            self.lighting["controls"],
            {name: control.get("caption", name) for name, control in self.legacy["controls"].items()},
        )

    def test_classification_contract_is_advisory_and_deterministic(self):
        self.assertEqual(classify_scene({"description": "Fixed blue", "lights": []}), "static")
        self.assertEqual(
            classify_scene({
                "description": "Slow wave",
                "lights": [{"modulator": "time"}],
            }),
            "normal",
        )
        self.assertEqual(
            classify_scene({
                "description": "Audio reaction",
                "lights": [{"modulator": "mfft"}],
            }),
            "responsive",
        )

    def test_committed_application_config_matches_generated_migration(self):
        self.assertEqual(self.application.get("lighting"), self.lighting)


if __name__ == "__main__":
    unittest.main()
