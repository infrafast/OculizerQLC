import json
import tempfile
import unittest
from pathlib import Path

from oculizer.light.qlc_config import QLCConfig, QLCConfigError


class QLCConfigTests(unittest.TestCase):
    def test_loads_transport_controls_and_routing_from_one_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qlc_config.json"
            path.write_text(
                json.dumps({
                    "transport": {"host": "192.0.2.10", "port": 7777, "dry_run": True},
                    "websocket": {"host": "192.0.2.11", "port": 9998, "dry_run": True},
                    "controls": {
                        "master": {"OSCPath": "/oculizer/master", "caption": "Master"},
                        "bass": {"OSCPath": "/oculizer/bass", "caption": "Bass"},
                    },
                    "routing": {
                        "pulse_seconds": 0.2,
                        "scenes": {
                            "announcement": {
                                "OSCaction": "pushButton",
                                "OSCPath": "/oculizer/scenes/announcement",
                            },
                            "silent": {"OSCaction": "pushButton", "OSCPath": "/oculizer/scenes/silent"},
                        },
                    },
                }),
                encoding="utf-8",
            )
            config = QLCConfig.from_file(path)

        self.assertEqual(config.transport.host, "192.0.2.10")
        self.assertEqual(config.transport.port, 7777)
        self.assertTrue(config.transport.dry_run)
        self.assertEqual(config.websocket.host, "192.0.2.11")
        self.assertEqual(config.websocket.port, 9998)
        self.assertTrue(config.websocket.dry_run)
        self.assertEqual(config.controls["master"].osc_path, "/oculizer/master")
        self.assertEqual(config.controls["master"].caption, "Master")
        self.assertEqual(config.controls["bass"].osc_path, "/oculizer/bass")
        self.assertEqual(config.routing.pulse_seconds, 0.2)
        self.assertEqual(config.routing.get("announcement").osc_path, "/oculizer/scenes/announcement")
        self.assertEqual(config.routing.get("silent").osc_action, "pushButton")

    def test_rejects_invalid_sections_and_controls(self):
        invalid_configs = (
            {"transport": []},
            {"controls": []},
            {"routing": []},
            {"websocket": []},
            {"websocket": {"port": 70000}},
            {"controls": {"master": "/legacy"}},
            {"controls": {"master": {"OSCPath": "master"}}},
            {"routing": {"scenes": {"party": {"OSCPath": "party"}}}},
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
        self.assertIsNone(config.controls["master"].osc_path)
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
