import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
SCENES_DIR = PROJECT_ROOT / "scenes"
V6_MAPPING = PROJECT_ROOT / "oculizer" / "scene_predictors" / "v6" / "scene_mapping.json"
FIFTEEN_SECOND_SCENES = {
    "discodream",
    "discolaser",
    "green_speedracer",
    "orb_racer",
    "red_speedracer",
    "white_speedracer",
}


def has_active_strobe(value):
    if isinstance(value, dict):
        return any(
            ("strobe" in key.lower() and child not in (0, "0", None, False))
            or has_active_strobe(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(has_active_strobe(child) for child in value)
    return False


class V6SceneDurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mapping = json.loads(V6_MAPPING.read_text(encoding="utf-8"))
        cls.scenes = {
            name: json.loads((SCENES_DIR / f"{name}.json").read_text(encoding="utf-8"))
            for name in set(mapping.values())
        }

    def test_every_active_strobe_scene_is_limited_to_eight_seconds(self):
        active_strobes = {
            name for name, scene in self.scenes.items() if has_active_strobe(scene)
        }
        self.assertEqual(
            {name for name, scene in self.scenes.items() if scene.get("max_duration_seconds") == 8},
            active_strobes,
        )

    def test_alternating_or_high_energy_non_strobe_scenes_use_fifteen_seconds(self):
        self.assertEqual(
            {name for name, scene in self.scenes.items() if scene.get("max_duration_seconds") == 15},
            FIFTEEN_SECOND_SCENES,
        )

