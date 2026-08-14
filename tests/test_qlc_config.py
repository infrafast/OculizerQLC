import json
import tempfile
import unittest
from pathlib import Path

from oculizer.light.qlc_config import QLCConfig, QLCConfigError


class QLCConfigTests(unittest.TestCase):
    def test_rejects_invalid_sections_and_controls(self):
        invalid_configs = (
            {},
            {"lighting": []},
            {"lighting": {"controls": []}},
            {"lighting": {"native": {"port": 70000}}},
            {"lighting": {"controls": {"master": {"caption": "Master"}}}},
        )
        for data in invalid_configs:
            with self.subTest(data=data), self.assertRaises(QLCConfigError):
                QLCConfig.from_mapping(data)

    def test_loads_native_lighting_from_application_config(self):
        config = QLCConfig.from_mapping({
            "audio": {},
            "lighting": {
                "native": {"host": "192.0.2.20", "dry_run": True},
                "controls": {"master": "Grand Master"},
                "routing": {
                    "pulse_seconds": 0.2,
                    "fallback_scene": "ambient1",
                    "caption_overrides": {"silent": "Silence"},
                },
                "scene_metadata": {
                    "ambient1": {
                        "description": "Fallback look",
                        "design_behavior": "normal",
                    },
                    "silent": {
                        "description": "Silent look",
                        "design_behavior": "static",
                        "max_duration_seconds": 12,
                    },
                },
            },
        })

        self.assertEqual(config.native.host, "192.0.2.20")
        self.assertTrue(config.native.dry_run)
        self.assertEqual(config.controls["master"].caption, "Grand Master")
        self.assertEqual(config.routing.get("silent").caption, "Silence")
        self.assertEqual(config.routing.resolve("unknown"), "ambient1")
        self.assertEqual(config.scene_metadata["silent"]["max_duration_seconds"], 12)

    def test_rejects_invalid_native_scene_metadata(self):
        base = {
            "lighting": {
                "scene_metadata": {
                    "ambient1": {
                        "description": "Fallback look",
                        "design_behavior": "normal",
                    }
                }
            }
        }
        invalid = json.loads(json.dumps(base))
        invalid["lighting"]["scene_metadata"]["ambient1"]["design_behavior"] = "fast"
        with self.assertRaisesRegex(QLCConfigError, "design_behavior"):
            QLCConfig.from_mapping(invalid)


if __name__ == "__main__":
    unittest.main()
