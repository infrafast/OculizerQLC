import unittest
from unittest.mock import patch

import oculize
import oculizer_service


class SceneCacheCliTests(unittest.TestCase):
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
