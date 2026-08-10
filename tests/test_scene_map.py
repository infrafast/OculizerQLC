import json
import tempfile
import unittest
from pathlib import Path

from oculizer.light.scene_map import SceneMap, SceneMapError


class SceneMapTests(unittest.TestCase):
    def test_loads_push_button_routes(self):
        scene_map = SceneMap.from_mapping(
            {
                "pulse_seconds": 0.2,
                "unmapped": "error",
                "scenes": {
                    "party": {"OSCPath": "/show/party"},
                    "silent": {"OSCPath": "/oculizer/scenes/silent"},
                },
            }
        )

        self.assertEqual(scene_map.get("party").osc_path, "/show/party")
        self.assertEqual(scene_map.get("silent").osc_action, "pushButton")
        self.assertEqual(scene_map.get("silent").osc_path, "/oculizer/scenes/silent")
        self.assertEqual(scene_map.pulse_seconds, 0.2)
        self.assertEqual(scene_map.unmapped, "error")

    def test_resolves_unmapped_scene_to_configured_fallback(self):
        scene_map = SceneMap.from_mapping(
            {
                "unmapped": "fallback",
                "fallback_scene": "party",
                "scenes": {"party": {"OSCPath": "/party"}},
            }
        )

        self.assertEqual(scene_map.resolve("wave"), "party")
        self.assertEqual(scene_map.resolve("party"), "party")

    def test_loads_from_json_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenes.json"
            path.write_text(
                json.dumps({"scenes": {"party": {"OSCPath": "/test"}}}),
                encoding="utf-8",
            )
            scene_map = SceneMap.from_file(path)

        self.assertEqual(scene_map.get("party").osc_path, "/test")

    def test_rejects_invalid_controls(self):
        invalid_maps = (
            {"pulse_seconds": -1},
            {"unmapped": "guess"},
            {"unmapped": "fallback", "scenes": {"party": {"OSCPath": "/party"}}},
            {"fallback_scene": "party", "scenes": {"party": {"OSCPath": "/party"}}},
            {"scenes": {"party": {"OSCPath": "test"}}},
            {"scenes": {"party": {"OSCaction": "unknown"}}},
            {"scenes": {"silent": {"OSCaction": "off", "OSCPath": "/oculizer/scenes/silent"}}},
            {"scenes": {"party": {"OSCaction": "toggle", "OSCPath": "/party"}}},
            {"scenes": {"party": {"path": "/legacy"}}},
            {"scenes": {"party": {"action": "toggle", "OSCPath": "/party"}}},
        )
        for data in invalid_maps:
            with self.subTest(data=data), self.assertRaises(SceneMapError):
                SceneMap.from_mapping(data)


if __name__ == "__main__":
    unittest.main()
