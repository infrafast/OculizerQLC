import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from oculizer.light.backends import (
    EnttecBackend,
    QLCOscBackend,
    create_qlc_osc_backend,
)
from oculizer.light.control import Oculizer


class EnttecBackendTests(unittest.TestCase):
    def test_blackout_and_idempotent_close_delegate_to_controller(self):
        controller = Mock()
        backend = EnttecBackend(controller, {"fixture": object()})

        self.assertTrue(backend.supports_direct_fixture_output)
        self.assertTrue(backend.blackout())
        backend.close()
        backend.close()

        controller.blackout.assert_called_once_with()
        controller.close.assert_called_once_with()


class QLCOscBackendTests(unittest.TestCase):
    def test_intent_parameter_blackout_and_close_delegate_to_osc_client(self):
        client = Mock()
        client.set_level.return_value = True
        client.blackout.return_value = True
        backend = QLCOscBackend(client)

        self.assertFalse(backend.supports_direct_fixture_output)
        self.assertTrue(backend.set_parameter("master", 0.5))
        self.assertTrue(backend.blackout(False))
        backend.close()

        client.set_level.assert_called_once_with("/oculizer/master", 0.5)
        client.blackout.assert_called_once_with(False)
        client.close.assert_called_once_with()

    def test_factory_applies_runtime_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "osc.json"
            config_path.write_text(
                json.dumps({"host": "192.0.2.1", "port": 7700}),
                encoding="utf-8",
            )
            backend = create_qlc_osc_backend(
                config_path,
                host="127.0.0.1",
                port=9000,
                dry_run=True,
            )

        self.assertEqual(backend.client.config.host, "127.0.0.1")
        self.assertEqual(backend.client.config.port, 9000)
        self.assertTrue(backend.client.config.dry_run)
        self.assertIsNone(backend.client._socket)
        backend.close()


class OculizerBackendSelectionTests(unittest.TestCase):
    def test_qlc_osc_mode_never_loads_the_enttec_controller(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "osc.json"
            config_path.write_text(
                json.dumps({"host": "127.0.0.1", "port": 7700, "dry_run": True}),
                encoding="utf-8",
            )
            with (
                patch.object(
                    Oculizer,
                    "_get_audio_device_idx",
                    side_effect=AssertionError("Audio must not be initialized"),
                ) as get_audio_device,
                patch.object(Oculizer, "_load_profile", return_value={"lights": []}),
                patch.object(
                    Oculizer,
                    "_load_controller",
                    side_effect=AssertionError("Enttec must not be initialized"),
                ) as load_controller,
            ):
                controller = Oculizer(
                    "testing",
                    Mock(),
                    output="qlc-osc",
                    osc_config_path=config_path,
                )

        self.assertIsNone(controller.dmx_controller)
        self.assertEqual(controller.controller_dict, {})
        self.assertFalse(controller.audio_processing_enabled)
        self.assertIsNone(controller.device_idx)
        get_audio_device.assert_not_called()
        load_controller.assert_not_called()
        controller.start()
        self.assertTrue(controller.running.wait(timeout=1.0))
        controller.stop()
        controller.join(timeout=1.0)
        self.assertFalse(controller.is_alive())


if __name__ == "__main__":
    unittest.main()
