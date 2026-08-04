import unittest
import json
from pathlib import Path

import numpy as np
from efficientat import labels

from oculizer.scene_predictors.v4.predictor import ScenePredictor as V4ScenePredictor
from oculizer.scene_predictors.v5.predictor import ScenePredictor as V5ScenePredictor


class SpeechScoreTests(unittest.TestCase):
    def test_singing_contributes_to_music_not_speech(self):
        scores = np.zeros(len(labels), dtype=np.float32)
        scores[labels.index("Singing")] = 0.9
        scores[labels.index("Speech")] = 0.2
        scores[labels.index("Music")] = 0.4

        for predictor in (V4ScenePredictor, V5ScenePredictor):
            with self.subTest(predictor=predictor.__module__):
                result = predictor.aggregate_audioset_scores(scores)
                self.assertAlmostEqual(result["speech"], 0.2)
                self.assertAlmostEqual(result["singing"], 0.9)
                self.assertAlmostEqual(result["music"], 0.9)

    def test_v5_uses_the_complete_v4_scene_mapping(self):
        predictor_root = Path(__file__).parents[1] / "oculizer" / "scene_predictors"
        v4_mapping = json.loads((predictor_root / "v4" / "scene_mapping.json").read_text())
        v5_mapping = json.loads((predictor_root / "v5" / "scene_mapping.json").read_text())

        self.assertEqual(v5_mapping, v4_mapping)
        self.assertEqual(len(v5_mapping), 100)
        self.assertNotIn("placeholder", v5_mapping.values())


if __name__ == "__main__":
    unittest.main()
