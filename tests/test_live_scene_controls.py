import unittest

from oculize import AudioOculizerController


class LiveSceneControlEditorTests(unittest.TestCase):
    def test_cache_is_bounded_between_one_and_one_hundred(self):
        values = {"cache": 1, "rate": None, "throttle": None}
        AudioOculizerController._adjust_limit_value(values, 0, -1)
        self.assertEqual(values["cache"], 1)
        values["cache"] = 100
        AudioOculizerController._adjust_limit_value(values, 0, 1)
        self.assertEqual(values["cache"], 100)

    def test_adjusting_disabled_policies_enables_safe_defaults(self):
        values = {"cache": 25, "rate": None, "throttle": None}

        AudioOculizerController._adjust_limit_value(values, 1, 1)
        AudioOculizerController._adjust_limit_value(values, 4, -1)

        self.assertEqual(values["rate"], (5, 5.0))
        self.assertEqual(values["throttle"], (3, 1.5))

    def test_policy_counts_and_times_never_reach_zero(self):
        values = {"cache": 25, "rate": (1, 0.5), "throttle": (1, 0.5)}
        for selected in (1, 2, 3, 4):
            AudioOculizerController._adjust_limit_value(values, selected, -1)

        self.assertEqual(values["rate"], (1, 0.5))
        self.assertEqual(values["throttle"], (1, 0.5))


if __name__ == "__main__":
    unittest.main()
