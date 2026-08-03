import json
import tempfile
import unittest
from pathlib import Path

from oculizer.light.scene_map import SceneMap, SceneMapError


class SceneMapTests(unittest.TestCase):
    def test_loads_toggle_off_and_blackout_controls(self):
        scene_map = SceneMap.from_mapping(
            {
                "pulse_seconds": 0.2,
                "unmapped": "error",
                "scenes": {
                    "party": {"path": "/show/party"},
                    "off": {"action": "off"},
                    "blackout": {"action": "blackout"},
                },
            }
        )

        self.assertEqual(scene_map.get("party").path, "/show/party")
        self.assertEqual(scene_map.get("off").action, "off")
        self.assertEqual(scene_map.get("blackout").action, "blackout")
        self.assertEqual(scene_map.pulse_seconds, 0.2)
        self.assertEqual(scene_map.unmapped, "error")

    def test_resolves_unmapped_scene_to_configured_fallback(self):
        scene_map = SceneMap.from_mapping(
            {
                "unmapped": "fallback",
                "fallback_scene": "party",
                "scenes": {"party": {"path": "/party"}},
            }
        )

        self.assertEqual(scene_map.resolve("wave"), "party")
        self.assertEqual(scene_map.resolve("party"), "party")

    def test_loads_from_json_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenes.json"
            path.write_text(
                json.dumps({"scenes": {"party": {"path": "/test"}}}),
                encoding="utf-8",
            )
            scene_map = SceneMap.from_file(path)

        self.assertEqual(scene_map.get("party").path, "/test")

    def test_rejects_invalid_controls(self):
        invalid_maps = (
            {"pulse_seconds": -1},
            {"unmapped": "guess"},
            {"unmapped": "fallback", "scenes": {"party": {"path": "/party"}}},
            {"fallback_scene": "party", "scenes": {"party": {"path": "/party"}}},
            {"scenes": {"party": {"path": "test"}}},
            {"scenes": {"party": {"action": "unknown"}}},
        )
        for data in invalid_maps:
            with self.subTest(data=data), self.assertRaises(SceneMapError):
                SceneMap.from_mapping(data)


if __name__ == "__main__":
    unittest.main()
