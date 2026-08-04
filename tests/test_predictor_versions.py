import json
import unittest
from pathlib import Path

from oculizer.scene_predictors import AVAILABLE_VERSIONS, get_predictor, list_available_versions
from oculizer.scene_predictors.v4.predictor import ScenePredictor as V4ScenePredictor


class PredictorVersionTests(unittest.TestCase):
    def test_only_complete_predictors_are_available(self):
        self.assertEqual(AVAILABLE_VERSIONS, ["v4", "v5"])
        self.assertEqual(list_available_versions(), ["v4", "v5"])
        self.assertIs(get_predictor(), V4ScenePredictor)
        with self.assertRaisesRegex(ValueError, "not available"):
            get_predictor("v1")

    def test_legacy_mappings_are_preserved(self):
        root = Path(__file__).parents[1] / "oculizer" / "scene_predictors" / "legacy_mappings"
        expected_lengths = {"v1": 100, "v3": 120, "vday": 100}
        for version, expected_length in expected_lengths.items():
            with self.subTest(version=version):
                mapping = json.loads((root / f"{version}_scene_mapping.json").read_text())
                self.assertEqual(len(mapping), expected_length)
                self.assertEqual(set(mapping), {str(index) for index in range(expected_length)})


if __name__ == "__main__":
    unittest.main()
