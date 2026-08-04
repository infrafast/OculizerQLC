import argparse
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
import oculizer.scene_predictors.v6.predictor as v6_predictor_module

from scripts.train_predictor_v6 import (
    FEATURE_SCHEMA,
    WindowRecord,
    load_cache,
    load_mapping,
    save_cache,
    window_starts,
)
from oculizer.scene_predictors.v4.predictor import ScenePredictor as V4ScenePredictor
from oculizer.scene_predictors.v6.predictor import ScenePredictor as V6ScenePredictor


class TrainPredictorV6Tests(unittest.TestCase):
    def test_v6_uses_the_v4_feature_contract(self):
        self.assertTrue(issubclass(V6ScenePredictor, V4ScenePredictor))
        self.assertEqual(FEATURE_SCHEMA, "efficientat-dymn20_as-1920+v4-mfcc-mean-128")

    def test_v6_defaults_to_its_own_model_directory(self):
        with patch.object(V4ScenePredictor, "__init__", return_value=None) as parent_init:
            V6ScenePredictor()
        model_dir = parent_init.call_args.kwargs["model_dir"]
        self.assertEqual(model_dir, Path(v6_predictor_module.__file__).parent)
        self.assertEqual(parent_init.call_args.kwargs["sr"], 48000)

    def test_window_starts_include_end_and_limit_evenly(self):
        self.assertEqual(window_starts(8, 4, 2, 0).tolist(), [0, 2, 4])
        self.assertEqual(window_starts(20, 4, 2, 3).tolist(), [0, 8, 16])
        self.assertEqual(window_starts(2, 4, 2, 0).tolist(), [0])

    def test_mapping_requires_every_cluster(self):
        self.assertEqual(load_mapping(None, 2), {"0": "party", "1": "party"})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mapping.json"
            path.write_text('{"0": "party"}')
            with self.assertRaisesRegex(ValueError, "exactly"):
                load_mapping(path, 2)

    def test_feature_cache_contract_round_trip(self):
        args = argparse.Namespace(sample_rate=48000, window_seconds=4.0, hop_seconds=2.0)
        features = np.arange(8, dtype=np.float32).reshape(2, 4)
        records = [
            WindowRecord("one.wav", 0.0, 0.1, 0.2, 0.3, 0.4),
            WindowRecord("two.wav", 2.0, 0.5, 0.6, 0.7, 0.8),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "features.npz"
            save_cache(path, features, records, args)
            loaded_features, loaded_records = load_cache(path, args)
        np.testing.assert_array_equal(loaded_features, features)
        self.assertEqual(loaded_records, records)


if __name__ == "__main__":
    unittest.main()
