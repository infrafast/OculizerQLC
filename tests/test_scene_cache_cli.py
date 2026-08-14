import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import oculize
import oculizer_service


class SceneCacheCliTests(unittest.TestCase):
    def test_both_entry_points_reject_missing_audio_file_cleanly(self):
        missing = Path("missing-test-audio.wav").resolve()
        for module, program in (
            (oculize, "oculize.py"),
            (oculizer_service, "oculizer_service.py"),
        ):
            stderr = io.StringIO()
            with self.subTest(program=program), \
                    patch("sys.argv", [program, "--audio-file", str(missing)]), \
                    redirect_stderr(stderr), \
                    self.assertRaises(SystemExit) as exit_error:
                module.parse_args()
            self.assertEqual(exit_error.exception.code, 2)
            self.assertIn(f"--audio-file does not exist: {missing}", stderr.getvalue())

    def test_headless_startup_reports_expected_input_error_without_traceback(self):
        with patch("oculizer_service.parse_args", return_value=object()), \
                patch("oculizer_service.configure_service_streams"), \
                patch("oculizer_service.build_service", side_effect=ValueError("invalid WAV")):
            self.assertEqual(oculizer_service.main(), 2)

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

    def test_linux_uses_one_default_os_audio_stream(self):
        with patch("oculize.platform.system", return_value="Linux"), \
                patch("sys.argv", ["oculize.py", "--input-device", "default"]):
            args = oculize.parse_args()
        self.assertTrue(args.single_stream)
        self.assertIsNone(args.default_prediction_device)
        self.assertEqual(args.input_device, "default")

    def test_windows_keeps_its_prediction_device_available_after_parsing(self):
        with patch("oculize.platform.system", return_value="Windows"), \
                patch("sys.argv", ["oculize.py"]):
            args = oculize.parse_args()
        self.assertFalse(args.single_stream)
        self.assertEqual(args.default_prediction_device, "cable_output")

    def test_linux_explicit_prediction_device_can_enable_dual_stream(self):
        with patch("oculize.platform.system", return_value="Linux"), \
                patch("sys.argv", ["oculize.py", "--prediction-device", "USB Capture"]):
            args = oculize.parse_args()
        self.assertTrue(args.single_stream)
        self.assertFalse(args.single_stream_explicit)

    def test_explicit_single_stream_wins_over_prediction_device(self):
        with patch("oculize.platform.system", return_value="Linux"), \
                patch("sys.argv", ["oculize.py", "--single-stream", "--prediction-device", "USB Capture"]):
            args = oculize.parse_args()
        self.assertTrue(args.single_stream)
        self.assertTrue(args.single_stream_explicit)

    def test_headless_default_and_explicit_value(self):
        with patch("sys.argv", ["oculizer_service.py"]):
            self.assertEqual(oculizer_service.parse_args().scene_cache_size, 10)
        with patch("sys.argv", ["oculizer_service.py", "--scene-cache-size", "6"]):
            self.assertEqual(oculizer_service.parse_args().scene_cache_size, 6)


if __name__ == "__main__":
    unittest.main()
