import json
import unittest
from pathlib import Path

from oculizer.scene_predictors import AVAILABLE_VERSIONS, get_predictor, list_available_versions
from oculizer.scene_predictors.v4.predictor import ScenePredictor as V4ScenePredictor


class PredictorVersionTests(unittest.TestCase):
    def test_only_complete_predictors_are_available(self):
        self.assertEqual(AVAILABLE_VERSIONS, list_available_versions())
        self.assertEqual(list_available_versions()[:2], ["v4", "v5"])
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

    def test_v6_availability_follows_complete_artefacts(self):
        root = Path(__file__).parents[1] / "oculizer" / "scene_predictors" / "v6"
        required = ("scaler.pkl", "pca.pkl", "kmeans.pkl", "scene_mapping.json", ".ready")
        complete = all((root / filename).is_file() for filename in required)
        self.assertEqual("v6" in list_available_versions(), complete)
        if complete:
            self.assertEqual(get_predictor("v6").__module__, "oculizer.scene_predictors.v6.predictor")
        else:
            with self.assertRaisesRegex(ValueError, "not available"):
                get_predictor("v6")


if __name__ == "__main__":
    unittest.main()
