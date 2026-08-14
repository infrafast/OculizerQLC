import json
import tempfile
import unittest
from pathlib import Path

from oculizer.scenes import LogicalSceneRegistry


def application_config(scenes, fallback="ambient1"):
    return {
        "lighting": {
            "native": {"dry_run": True},
            "controls": {"master": "master"},
            "routing": {"fallback_scene": fallback},
            "scene_metadata": scenes,
        }
    }


class LogicalSceneRegistryTests(unittest.TestCase):
    def write_config(self, path, scenes):
        path.write_text(json.dumps(application_config(scenes)), encoding="utf-8")

    def test_loads_only_compact_metadata_and_selects_party(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oculizer.json"
            self.write_config(path, {
                "ambient1": {"description": "Ambient", "design_behavior": "normal"},
                "party": {
                    "description": "Party",
                    "design_behavior": "responsive",
                    "max_duration_seconds": 8,
                },
            })
            registry = LogicalSceneRegistry(path)

        self.assertEqual(registry.current_scene["name"], "party")
        self.assertEqual(registry.scenes["party"]["max_duration_seconds"], 8)
        self.assertNotIn("lights", registry.scenes["party"])

    def test_reload_preserves_selection_and_is_atomic_on_invalid_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oculizer.json"
            scenes = {
                "ambient1": {"description": "Ambient", "design_behavior": "normal"},
                "party": {"description": "Party", "design_behavior": "responsive"},
            }
            self.write_config(path, scenes)
            registry = LogicalSceneRegistry(path)
            registry.set_scene("ambient1")
            scenes["wave"] = {"description": "Wave", "design_behavior": "normal"}
            self.write_config(path, scenes)
            registry.reload_scenes()
            self.assertEqual(registry.current_scene["name"], "ambient1")
            self.assertIn("wave", registry.scenes)

            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                registry.reload_scenes()
            self.assertEqual(registry.current_scene["name"], "ambient1")
            self.assertIn("wave", registry.scenes)

    def test_rejects_unknown_scene(self):
        registry = LogicalSceneRegistry()
        with self.assertRaisesRegex(ValueError, "not found"):
            registry.set_scene("does-not-exist")


if __name__ == "__main__":
    unittest.main()
