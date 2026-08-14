import logging
import unittest
from unittest.mock import Mock, patch

import oculize


class TerminalInitializationTests(unittest.TestCase):
    def test_interactive_log_rows_are_decoupled_from_web_log_retention(self):
        self.assertEqual(oculize.INTERACTIVE_LOG_ROWS, 9)
        self.assertLess(oculize.INTERACTIVE_LOG_ROWS, 50)

    def test_safe_terminal_write_clips_text_and_rejects_invalid_coordinates(self):
        screen = Mock()
        screen.getmaxyx.return_value = (8, 20)
        controller = Mock(stdscr=screen)

        written = oculize.AudioOculizerController._safe_addstr(
            controller, 2, 17, "abcdef", 42
        )
        rejected = oculize.AudioOculizerController._safe_addstr(
            controller, -1, 0, "outside", 42
        )

        self.assertTrue(written)
        self.assertFalse(rejected)
        screen.addstr.assert_called_once_with(2, 17, "ab", 42)

    def test_safe_terminal_write_tolerates_concurrent_resize_error(self):
        screen = Mock()
        screen.getmaxyx.return_value = (8, 20)
        screen.addstr.side_effect = oculize.curses.error("resized")
        controller = Mock(stdscr=screen)

        self.assertFalse(oculize.AudioOculizerController._safe_addstr(
            controller, 1, 0, "status", 42
        ))

    def test_keyboard_interrupt_cleanup_temporarily_ignores_sigint(self):
        controller = Mock()
        with (
            patch("oculize.signal.getsignal", return_value="previous") as get_signal,
            patch("oculize.signal.signal") as set_signal,
        ):
            oculize._stop_after_keyboard_interrupt(controller)

        get_signal.assert_called_once_with(oculize.signal.SIGINT)
        self.assertEqual(set_signal.call_args_list, [
            unittest.mock.call(oculize.signal.SIGINT, oculize.signal.SIG_IGN),
            unittest.mock.call(oculize.signal.SIGINT, "previous"),
        ])
        controller.stop.assert_called_once_with()

    def test_interactive_native_forwards_encryption_key(self):
        screen = Mock()
        scene_manager = Mock()
        oculizer = Mock()

        with (
            patch("oculize.curses.curs_set"),
            patch("oculize.LogicalSceneRegistry", return_value=scene_manager),
            patch("oculize.Oculizer", return_value=oculizer) as constructor,
            patch("oculize.AutomaticSceneRouter"),
            patch("oculize.MasterModulator"),
            patch("oculize.FrequencyBandModulator"),
            patch("oculize.RuntimeControl"),
        ):
            controller = oculize.AudioOculizerController(
                screen,
                input_device="default",
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
                ["Lighting: QLC+ Native", "Audio: WAV file test.wav", "Predictor: v4"],
            )

        rendered = [call.args[2] for call in screen.addstr.call_args_list]
        self.assertIn("Loading Oculizer...", rendered)
        self.assertIn("Lighting: QLC+ Native", rendered)
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
