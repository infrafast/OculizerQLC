import io
import unittest
from unittest.mock import patch

from oculizer.light.control import Oculizer, _terminal_line
from oculizer.light.virtual_enttec_controller import VirtualEnttecController
from oculizer.scenes import SceneManager


class VirtualEnttecControllerTests(unittest.TestCase):
    def test_terminal_line_uses_carriage_return_and_newline(self):
        output = io.StringIO()

        with patch("sys.stdout", output):
            _terminal_line("DMX ready")

        self.assertEqual(output.getvalue(), "DMX ready\r\n")

    def test_logs_only_changed_channels_at_bounded_rate(self):
        now = [0.0]
        controller = VirtualEnttecController(log_rate_hz=5.0, clock=lambda: now[0])

        with self.assertLogs("oculizer.light.virtual_enttec_controller", level="INFO") as captured:
            controller.dmx_data[1] = 255
            controller._send_dmx_packet()
            controller.dmx_data[2] = 128
            controller._send_dmx_packet()
            now[0] = 1.0 / 3.0
            controller._send_dmx_packet()

        self.assertEqual(len(captured.output), 2)
        self.assertIn("{1: 255}", captured.output[0])
        self.assertIn("{2: 128}", captured.output[1])

    def test_frame_logging_can_be_disabled(self):
        controller = VirtualEnttecController(log_frames=False)
        controller.dmx_data[1] = 255

        with self.assertNoLogs(
            "oculizer.light.virtual_enttec_controller", level="INFO"
        ):
            controller._send_dmx_packet()
            controller.close()

        self.assertEqual(controller.log_rate_hz, 3.0)

    def test_close_emits_final_blackout_once(self):
        controller = VirtualEnttecController()
        controller.dmx_data[10] = 200
        controller._last_logged_frame = list(controller.dmx_data)

        with self.assertLogs("oculizer.light.virtual_enttec_controller", level="INFO") as captured:
            controller.close()
            controller.close()

        self.assertEqual(len(captured.output), 1)
        self.assertIn("{10: 0}", captured.output[0])


class EnttecDryRunSelectionTests(unittest.TestCase):
    def test_dry_run_skips_serial_detection_and_builds_profile_fixtures(self):
        manager = SceneManager("scenes", profile_name="garage2025")
        with (
            patch(
                "oculizer.light.control.get_dmx_config",
                side_effect=AssertionError("serial discovery must not run"),
            ),
            patch.object(Oculizer, "_get_audio_device_idx", return_value=None),
        ):
            engine = Oculizer(
                "garage2025",
                manager,
                scene_prediction_enabled=False,
                output="enttec",
                dmx_dry_run=True,
            )

        self.assertIsInstance(engine.dmx_controller, VirtualEnttecController)
        self.assertEqual(len(engine.controller_dict), 19)
        engine.stop()


if __name__ == "__main__":
    unittest.main()
