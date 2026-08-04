import unittest

from oculizer.scene_limits import describe_scene_limits


class SceneLimitDescriptionTests(unittest.TestCase):
    def test_absent_limits_add_no_startup_message(self):
        self.assertEqual(describe_scene_limits(), [])

    def test_rate_only_is_explained(self):
        lines = describe_scene_limits((4, 5.0), None)

        self.assertIn("max 4 changes/5s", lines[0])
        self.assertIn("moderate", lines[1])

    def test_throttle_only_is_explained(self):
        lines = describe_scene_limits(None, (3, 2.0))

        self.assertIn("burst 3, +1 credit/2s", lines[0])
        self.assertIn("generous bursts", lines[1])

    def test_slow_throttle_recovery_gets_recommendation(self):
        lines = describe_scene_limits(None, (2, 5.0))

        self.assertIn("recovery may feel slow", lines[1])
        self.assertIn("reduce recovery time", lines[2])

    def test_redundant_rate_limit_is_identified(self):
        lines = describe_scene_limits((8, 5.0), (3, 2.0))

        self.assertIn("~5 changes/5s", lines[1])
        self.assertIn("effectively redundant", lines[1])
        self.assertIn("omit --scene-rate-limit", lines[2])

    def test_complementary_limits_are_identified(self):
        lines = describe_scene_limits((6, 10.0), (3, 2.0))

        self.assertIn("complementary", lines[1])

    def test_rate_cap_can_limit_initial_burst(self):
        lines = describe_scene_limits((2, 10.0), (3, 2.0))

        self.assertIn("caps the initial burst", lines[1])


if __name__ == "__main__":
    unittest.main()
