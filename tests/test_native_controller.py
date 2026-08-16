import unittest
from unittest.mock import Mock, patch

from oculizer.light.native_controller import (
    NativeLightingController,
    create_native_lighting_controller,
)
from oculizer.light.qlc_config import QLCConfig


class NativeLightingControllerTests(unittest.TestCase):
    def test_runtime_status_caption_contains_time_public_mode_and_dynamic_profile(self):
        config = QLCConfig.from_file("config/oculizer.json")
        client = Mock()
        controller = NativeLightingController(
            client, config.routing, config.controls,
            "config/oculizer.json", config.scene_metadata,
        )

        with patch("oculizer.light.native_controller.time.strftime", return_value="12:34:56"):
            self.assertTrue(controller.set_runtime_status("selection", "calm"))

        client.set_status_caption.assert_called_once_with(
            "12:34:56 : Mode:selection, Dynamic:calm"
        )

    def test_scene_fallback_duplicate_suppression_and_slider_caption(self):
        config = QLCConfig.from_file("config/oculizer.json")
        client = Mock()
        client.activate_button.return_value = True
        client.set_slider_level.return_value = True
        controller = NativeLightingController(
            client,
            config.routing,
            config.controls,
            "config/oculizer.json",
            config.scene_metadata,
        )

        self.assertTrue(controller.activate_scene("wave"))
        self.assertTrue(controller.activate_scene("wave"))
        self.assertTrue(controller.activate_scene("unknown-prediction"))
        self.assertEqual(
            [call.args[0] for call in client.activate_button.call_args_list],
            ["wave", "ambient1"],
        )
        self.assertTrue(controller.set_parameter("master", 0.5))
        client.set_slider_level.assert_called_once_with("master", 0.5)

    def test_factory_dry_run_opens_no_network_and_loads_full_catalog(self):
        controller = create_native_lighting_controller(
            "config/oculizer.json", dry_run=True
        )
        self.addCleanup(controller.close)
        self.assertEqual(controller.client.state.value, "ready")
        self.assertEqual(len(controller.scene_map.scenes), 127)
        self.assertTrue(controller.activate_scene("party"))


if __name__ == "__main__":
    unittest.main()
