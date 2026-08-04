from collections import deque
import threading
import unittest

from oculizer.light.control import Oculizer


class CacheHolder:
    def __init__(self):
        self.prediction_lock = threading.Lock()
        self.scene_cache_size = 3
        self.scene_cache = deque(["wave", "party", "party"], maxlen=3)
        self.current_predicted_scene = "party"


class LiveSceneCacheTests(unittest.TestCase):
    def test_resize_preserves_newest_predictions_and_recomputes_mode(self):
        holder = CacheHolder()

        Oculizer.set_scene_cache_size(holder, 2)

        self.assertEqual(list(holder.scene_cache), ["party", "party"])
        self.assertEqual(holder.scene_cache.maxlen, 2)
        self.assertEqual(holder.current_predicted_scene, "party")

    def test_resize_rejects_out_of_range_values_without_mutation(self):
        holder = CacheHolder()

        with self.assertRaises(ValueError):
            Oculizer.set_scene_cache_size(holder, 0)

        self.assertEqual(holder.scene_cache_size, 3)
        self.assertEqual(list(holder.scene_cache), ["wave", "party", "party"])


if __name__ == "__main__":
    unittest.main()
