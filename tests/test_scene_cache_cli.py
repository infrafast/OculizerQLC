import unittest
from unittest.mock import patch

import oculize
import oculizer_service


class SceneCacheCliTests(unittest.TestCase):
    def test_both_entry_points_accept_no_named_dynamic_controls(self):
        empty_controls = {"control": {"dynamic_controls": {}}}
        with patch("sys.argv", ["oculize.py"]), \
                patch("oculize.load_runtime_config", return_value=empty_controls):
            args = oculize.parse_args()
            self.assertEqual(args.dynamic_control, "off")
            self.assertEqual(args.dynamic_controls, {})
        with patch("sys.argv", ["oculizer_service.py"]), \
                patch("oculizer_service.load_runtime_config", return_value=empty_controls):
            args = oculizer_service.parse_args()
            self.assertEqual(args.dynamic_control, "off")
            self.assertEqual(args.dynamic_controls, {})

    def test_v6_is_the_default_predictor_for_both_entry_points(self):
        with patch("sys.argv", ["oculize.py"]):
            self.assertEqual(oculize.parse_args().predictor_version, "v6")
        with patch("sys.argv", ["oculizer_service.py"]):
            self.assertEqual(oculizer_service.parse_args().predictor_version, "v6")

    def test_interactive_default_is_ten_on_every_platform(self):
        for platform_name in ("Darwin", "Linux", "Windows"):
            with self.subTest(platform=platform_name), \
                    patch("oculize.platform.system", return_value=platform_name), \
                    patch("sys.argv", ["oculize.py"]):
                self.assertEqual(oculize.parse_args().scene_cache_size, 10)

    def test_interactive_explicit_value_is_preserved(self):
        with patch("sys.argv", ["oculize.py", "--scene-cache-size", "4"]):
            self.assertEqual(oculize.parse_args().scene_cache_size, 4)

    def test_headless_default_and_explicit_value(self):
        with patch("sys.argv", ["oculizer_service.py"]):
            self.assertEqual(oculizer_service.parse_args().scene_cache_size, 10)
        with patch("sys.argv", ["oculizer_service.py", "--scene-cache-size", "6"]):
            self.assertEqual(oculizer_service.parse_args().scene_cache_size, 6)


if __name__ == "__main__":
    unittest.main()
