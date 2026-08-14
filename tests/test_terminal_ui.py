import logging
import unittest
from unittest.mock import Mock, patch

import oculize


class TerminalInitializationTests(unittest.TestCase):
    def test_interactive_native_forwards_encryption_key(self):
        screen = Mock()
        scene_manager = Mock()
        oculizer = Mock()

        with (
            patch("oculize.curses.curs_set"),
            patch("oculize.SceneManager", return_value=scene_manager),
            patch("oculize.Oculizer", return_value=oculizer) as constructor,
            patch("oculize.AutomaticSceneRouter"),
            patch("oculize.MasterModulator"),
            patch("oculize.FrequencyBandModulator"),
            patch("oculize.RuntimeControl"),
        ):
            controller = oculize.AudioOculizerController(
                screen,
                profile=None,
                input_device="default",
                dual_stream=False,
                output="qlc-native",
                qlc_encryption_key="secret-key",
            )

        self.addCleanup(logging.getLogger().removeHandler, controller.log_handler)
        self.assertEqual(constructor.call_args.kwargs["qlc_encryption_key"], "secret-key")
        oculizer.restrict_scenes_to_backend.assert_called_once_with()

    def test_loading_screen_describes_active_components(self):
        screen = Mock()
        screen.getmaxyx.return_value = (24, 100)

        with (
            patch.dict(oculize.COLOR_PAIRS, {"info": 7}),
            patch("oculize.curses.color_pair", return_value=42),
        ):
            oculize.show_loading_screen(
                screen,
                ["Lighting: QLC+ OSC", "Audio: WAV file test.wav", "Predictor: v4"],
            )

        rendered = [call.args[2] for call in screen.addstr.call_args_list]
        self.assertIn("Loading Oculizer...", rendered)
        self.assertIn("Lighting: QLC+ OSC", rendered)
        self.assertIn("Audio: WAV file test.wav", rendered)
        self.assertIn("This can take several seconds.", rendered)
        screen.refresh.assert_called_once_with()

    def test_loading_screen_accepts_dynamic_control_status(self):
        screen = Mock()
        screen.getmaxyx.return_value = (24, 100)

        with (
            patch.dict(oculize.COLOR_PAIRS, {"info": 7}),
            patch("oculize.curses.color_pair", return_value=42),
        ):
            oculize.show_loading_screen(
                screen,
                ["Dynamic control: normal"],
            )

        rendered = [call.args[2] for call in screen.addstr.call_args_list]
        self.assertIn("Dynamic control: normal", rendered)

    def test_python_warnings_are_captured_by_logging(self):
        with (
            patch("oculize.logging.FileHandler"),
            patch("oculize.logging.basicConfig"),
            patch("oculize.logging.captureWarnings") as capture_warnings,
        ):
            oculize.setup_logging()

        capture_warnings.assert_called_once_with(True)

    def test_initialization_applies_background_before_one_physical_clear(self):
        screen = Mock()

        with (
            patch.dict(oculize.COLOR_PAIRS, {"info": 7}),
            patch("oculize.curses.color_pair", return_value=42) as color_pair,
        ):
            oculize.initialize_screen(screen)

        color_pair.assert_called_once_with(7)
        screen.assert_has_calls([
            unittest.mock.call.bkgd(" ", 42),
            unittest.mock.call.clear(),
            unittest.mock.call.refresh(),
        ])


if __name__ == "__main__":
    unittest.main()
