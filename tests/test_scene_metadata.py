import json
import unittest
from collections import Counter
from pathlib import Path


CONFIG = Path(__file__).parents[1] / "config" / "oculizer.json"


class SceneMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lighting = json.loads(CONFIG.read_text(encoding="utf-8"))["lighting"]
        cls.metadata = cls.lighting["scene_metadata"]

    def test_complete_reviewed_catalog_is_retained(self):
        self.assertEqual(len(self.metadata), 127)
        self.assertTrue(all(data["description"].strip() for data in self.metadata.values()))
        self.assertEqual(
            Counter(data["design_behavior"] for data in self.metadata.values()),
            {"static": 7, "normal": 25, "responsive": 95},
        )

    def test_duration_overrides_and_native_only_schema_are_retained(self):
        durations = {
            name: data["max_duration_seconds"]
            for name, data in self.metadata.items()
            if "max_duration_seconds" in data
        }
        self.assertEqual(len(durations), 23)
        serialized = json.dumps(self.lighting)
        for obsolete in ("OSCPath", "OSCaction", "websocket", '"transport"'):
            self.assertNotIn(obsolete, serialized)

    def test_controls_and_fallback_are_valid_catalog_entries(self):
        self.assertEqual(set(self.lighting["controls"]), {"master", "bass", "mid", "high"})
        self.assertIn(self.lighting["routing"]["fallback_scene"], self.metadata)


if __name__ == "__main__":
    unittest.main()
