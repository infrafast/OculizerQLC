import json
import os
from pathlib import Path
import tempfile
import unittest

from oculizer.scenes.scene_manager import SceneManager


ROOT = Path(__file__).resolve().parents[1]
FALLBACKS_PATH = ROOT / "profiles" / "profile_fallbacks.json"


def profile_fixtures(profile_name):
    with (ROOT / "profiles" / f"{profile_name}.json").open(encoding="utf-8") as handle:
        profile = json.load(handle)
    return {light["name"] for light in profile["lights"]}


class ProfileFallbackTests(unittest.TestCase):
    def test_fallback_file_is_valid_and_references_existing_scenes(self):
        with FALLBACKS_PATH.open(encoding="utf-8") as handle:
            mappings = json.load(handle)

        scenes = {path.stem for path in (ROOT / "scenes").glob("*.json")}
        self.assertIsInstance(mappings, dict)
        self.assertEqual(len(mappings["mobile"]), 31)
        self.assertTrue(set(mappings["mobile"]).issubset(scenes))
        self.assertTrue(set(mappings["mobile"].values()).issubset(scenes))

    def test_mobile_loads_and_applies_its_fallbacks(self):
        manager = SceneManager(
            "scenes",
            profile_name="mobile",
            available_fixtures=profile_fixtures("mobile"),
        )

        self.assertEqual(len(manager.fallback_mappings), 31)
        self.assertFalse(manager.scene_compatibility["bass_hopper"])
        self.assertEqual(manager.get_fallback_scene("bass_hopper"), "sequence_ice")
        manager.set_scene("bass_hopper")
        self.assertEqual(manager.current_scene["name"], "sequence_ice")

    def test_garage_profile_has_no_fallbacks(self):
        manager = SceneManager(
            "scenes",
            profile_name="garage2025",
            available_fixtures=profile_fixtures("garage2025"),
        )

        self.assertEqual(manager.fallback_mappings, {})
        self.assertIsNone(manager.get_fallback_scene("bass_hopper"))

    def test_loading_is_independent_from_working_directory(self):
        previous_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as temporary_directory:
            try:
                os.chdir(temporary_directory)
                manager = SceneManager("scenes", profile_name="mobile")
            finally:
                os.chdir(previous_directory)

        self.assertEqual(len(manager.fallback_mappings), 31)


if __name__ == "__main__":
    unittest.main()
