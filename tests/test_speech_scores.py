import unittest

import numpy as np
from efficientat import labels

from oculizer.scene_predictors.v4.predictor import ScenePredictor


class SpeechScoreTests(unittest.TestCase):
    def test_singing_contributes_to_music_not_speech(self):
        scores = np.zeros(len(labels), dtype=np.float32)
        scores[labels.index("Singing")] = 0.9
        scores[labels.index("Speech")] = 0.2
        scores[labels.index("Music")] = 0.4

        result = ScenePredictor.aggregate_audioset_scores(scores)

        self.assertAlmostEqual(result["speech"], 0.2)
        self.assertAlmostEqual(result["singing"], 0.9)
        self.assertAlmostEqual(result["music"], 0.9)


if __name__ == "__main__":
    unittest.main()
