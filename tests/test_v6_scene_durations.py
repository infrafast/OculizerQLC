import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "oculizer.json"
V6_MAPPING = PROJECT_ROOT / "oculizer" / "scene_predictors" / "v6" / "scene_mapping.json"
FIFTEEN_SECOND_SCENES = {
    "discodream",
    "discolaser",
    "green_speedracer",
    "orb_racer",
    "red_speedracer",
    "white_speedracer",
}
EIGHT_SECOND_SCENES = {
    "bass_hopper_blue", "blue_bass_racer", "discobrain", "discodance",
    "electric", "fairies", "goosebumps", "hypno", "rainbow_pulse",
    "red_bass_pulse", "sequence_cosmic", "sequence_fire", "splatter",
    "sustain", "swamp", "temple", "white_riser",
}


class V6SceneDurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mapping = json.loads(V6_MAPPING.read_text(encoding="utf-8"))
        metadata = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["lighting"]["scene_metadata"]
        cls.scenes = {
            name: metadata[name]
            for name in set(mapping.values())
        }

    def test_every_active_strobe_scene_is_limited_to_eight_seconds(self):
        self.assertEqual(
            {name for name, scene in self.scenes.items() if scene.get("max_duration_seconds") == 8},
            EIGHT_SECOND_SCENES,
        )

    def test_alternating_or_high_energy_non_strobe_scenes_use_fifteen_seconds(self):
        self.assertEqual(
            {name for name, scene in self.scenes.items() if scene.get("max_duration_seconds") == 15},
            FIFTEEN_SECOND_SCENES,
        )
