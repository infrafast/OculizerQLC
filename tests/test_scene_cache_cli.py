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

    def test_interactive_default_cache_is_ten(self):
        with patch("sys.argv", ["oculize.py"]):
            self.assertEqual(oculize.parse_args().scene_cache_size, 10)

    def test_interactive_explicit_value_is_preserved(self):
        with patch("sys.argv", ["oculize.py", "--scene-cache-size", "4"]):
            self.assertEqual(oculize.parse_args().scene_cache_size, 4)

    def test_input_device_is_the_only_live_audio_selector(self):
        with patch("sys.argv", ["oculize.py", "--input-device", "USB Capture"]):
            args = oculize.parse_args()
        self.assertEqual(args.input_device, "USB Capture")
        self.assertFalse(args.average_dual_channels)
        for obsolete_attribute in (
            "single_stream", "dual_stream", "prediction_device", "prediction_channels",
        ):
            self.assertFalse(hasattr(args, obsolete_attribute))

    def test_both_entry_points_reject_removed_stream_options(self):
        for module, program in (
            (oculize, "oculize.py"),
            (oculizer_service, "oculizer_service.py"),
        ):
            for option in (
                "--single-stream", "--dual-stream", "--prediction-device", "--prediction-channels",
            ):
                stderr = io.StringIO()
                argv = [program, option]
                if option in ("--prediction-device", "--prediction-channels"):
                    argv.append("obsolete")
                with self.subTest(program=program, option=option), \
                        patch("sys.argv", argv), redirect_stderr(stderr), \
                        self.assertRaises(SystemExit) as exit_error:
                    module.parse_args()
                self.assertEqual(exit_error.exception.code, 2)
                self.assertIn("unrecognized arguments", stderr.getvalue())

    def test_headless_default_and_explicit_value(self):
        with patch("sys.argv", ["oculizer_service.py"]):
            self.assertEqual(oculizer_service.parse_args().scene_cache_size, 10)
        with patch("sys.argv", ["oculizer_service.py", "--scene-cache-size", "6"]):
            self.assertEqual(oculizer_service.parse_args().scene_cache_size, 6)

    def test_web_is_headless_only_and_can_be_disabled(self):
        with patch("sys.argv", ["oculizer_service.py"]):
            args = oculizer_service.parse_args()
        self.assertTrue(args.web_enabled)
        with patch("sys.argv", ["oculizer_service.py", "--no-web"]):
            args = oculizer_service.parse_args()
        self.assertFalse(args.web_enabled)
        with patch("sys.argv", ["oculize.py", "--no-web"]), \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            oculize.parse_args()


if __name__ == "__main__":
    unittest.main()
