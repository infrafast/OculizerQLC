import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from oculizer.light.backends import (
    EnttecBackend,
    QLCOscBackend,
    QLCWebSocketBackend,
    create_qlc_osc_backend,
    create_qlc_websocket_backend,
)
from oculizer.light.qlc_websocket import (
    QLCWebSocketConfig,
    QLCWebSocketClient,
    QLCWebSocketError,
)
from oculizer.light.control import Oculizer
from oculizer.light.qlc_config import QLCControl
from oculizer.light.scene_map import SceneMap


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
    def make_scene_map(self):
        return SceneMap.from_mapping(
            {
                "pulse_seconds": 0,
                "scenes": {
                    "party": {"OSCPath": "/party"},
                    "chill": {"OSCPath": "/chill"},
                    "off": {"OSCPath": "/blackout"},
                },
            }
        )

    def test_continuous_parameter_and_close_delegate_to_osc_client(self):
        client = Mock()
        client.set_level.return_value = True
        backend = QLCOscBackend(
            client, self.make_scene_map(),
            controls={"master": QLCControl("/show/master", "master")},
        )

        self.assertFalse(backend.supports_direct_fixture_output)
        self.assertTrue(backend.set_parameter("master", 0.5))
        backend.close()

        client.set_level.assert_called_once_with("/show/master", 0.5)
        client.close.assert_called_once_with()

    def test_initialize_and_close_emit_no_lighting_command(self):
        client = Mock()
        client.press.return_value = True
        client.release.return_value = True
        backend = QLCOscBackend(client, self.make_scene_map())

        self.assertTrue(backend.initialize())
        self.assertEqual(client.method_calls, [])
        self.assertTrue(backend.activate_scene("party"))
        backend.close()
        backend.close()

        self.assertEqual(backend.active_scene, "party")
        self.assertEqual(client.release.call_count, 1)
        client.close.assert_called_once_with()

    def test_scene_transition_activates_only_target_and_deduplicates(self):
        client = Mock()
        client.press.return_value = True
        client.release.return_value = True
        backend = QLCOscBackend(client, self.make_scene_map())

        self.assertTrue(backend.activate_scene("party"))
        self.assertTrue(backend.activate_scene("party"))
        self.assertTrue(backend.activate_scene("chill"))

        self.assertEqual(
            client.method_calls,
            [
                unittest.mock.call.press("/party"),
                unittest.mock.call.release("/party"),
                unittest.mock.call.press("/chill"),
                unittest.mock.call.release("/chill"),
            ],
        )
        self.assertEqual(backend.active_scene, "chill")

    def test_off_uses_its_configured_path_and_unmapped_scene_preserves_state(self):
        client = Mock()
        client.press.return_value = True
        client.release.return_value = True
        backend = QLCOscBackend(client, self.make_scene_map())

        self.assertTrue(backend.activate_scene("party"))
        self.assertFalse(backend.activate_scene("unknown"))
        self.assertEqual(backend.active_scene, "party")
        self.assertTrue(backend.activate_scene("off"))
        self.assertEqual(backend.active_scene, "off")
        self.assertEqual(client.method_calls[-2:], [
            unittest.mock.call.press("/blackout"),
            unittest.mock.call.release("/blackout"),
        ])

        self.assertTrue(backend.activate_scene("party"))

    def test_factory_applies_runtime_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "qlc_config.json"
            config_path.write_text(
                json.dumps({
                    "transport": {"host": "192.0.2.1", "port": 7700},
                    "routing": {"scenes": {"party": {"OSCPath": "/party"}}},
                }),
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


class QLCWebSocketBackendTests(unittest.TestCase):
    def test_activation_only_uses_exact_configured_caption(self):
        scene_map = SceneMap.from_mapping({"scenes": {
            "party": {"OSCPath": "/party", "caption": "Party Button"},
            "off": {"OSCPath": "/blackout", "caption": "Safe Off"},
            "announcement": {"OSCPath": "/announcement", "caption": "Speech"},
        }, "unmapped": "fallback", "fallback_scene": "party"})
        client = Mock()
        client.activate_button.return_value = True
        backend = QLCWebSocketBackend(client, scene_map)

        self.assertTrue(backend.activate_scene("party"))
        self.assertTrue(backend.activate_scene("party"))
        self.assertTrue(backend.activate_scene("off"))
        self.assertTrue(backend.activate_scene("announcement"))
        self.assertTrue(backend.activate_scene("unknown"))
        backend.close()

        self.assertEqual(client.activate_button.call_args_list, [
            unittest.mock.call("Party Button"), unittest.mock.call("Safe Off"),
            unittest.mock.call("Speech"), unittest.mock.call("Party Button"),
        ])
        self.assertEqual(backend.active_scene, "party")
        client.close.assert_called_once_with()

    def test_continuous_parameter_uses_configured_slider_caption(self):
        client = Mock()
        client.set_slider_level.return_value = True
        backend = QLCWebSocketBackend(
            client,
            SceneMap.from_mapping({"scenes": {"party": {"OSCPath": "/party"}}}),
            controls={"master": QLCControl("/oculizer/master", "Grand Master")},
        )

        self.assertTrue(backend.set_parameter("master", 0.5))
        self.assertFalse(backend.set_parameter("unknown", 0.5))
        client.set_slider_level.assert_called_once_with("Grand Master", 0.5)

    def test_repeated_activation_error_is_logged_only_once(self):
        scene_map = SceneMap.from_mapping({"scenes": {
            "off": {"OSCPath": "/blackout", "caption": "off"},
        }})
        client = Mock()
        client.activate_button.side_effect = QLCWebSocketError("wrong action")
        backend = QLCWebSocketBackend(client, scene_map)

        with self.assertLogs("oculizer.light.backends", level="ERROR") as logs:
            self.assertFalse(backend.activate_scene("off"))
            self.assertFalse(backend.activate_scene("off"))

        self.assertEqual(len(logs.records), 1)

    def test_factory_dry_run_opens_no_network_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qlc.json"
            path.write_text(json.dumps({
                "websocket": {"dry_run": True},
                "routing": {"scenes": {
                    "party": {"OSCPath": "/party", "caption": "Party Button"}
                }},
            }))
            factory = Mock(side_effect=AssertionError("must not connect"))
            backend = create_qlc_websocket_backend(path, websocket_factory=factory)

        self.assertTrue(backend.activate_scene("party"))
        factory.assert_not_called()
        backend.close()


class OculizerBackendSelectionTests(unittest.TestCase):
    def test_qlc_osc_mode_never_loads_the_enttec_controller(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "qlc_config.json"
            config_path.write_text(
                json.dumps({
                    "transport": {"host": "127.0.0.1", "port": 7700, "dry_run": True},
                    "routing": {"scenes": {"party": {"OSCPath": "/party"}}},
                }),
                encoding="utf-8",
            )
            with (
                patch.object(
                    Oculizer,
                    "_get_audio_device_idx",
                    side_effect=AssertionError("Audio must not be initialized"),
                ) as get_audio_device,
                patch.object(
                    Oculizer,
                    "_load_profile",
                    side_effect=AssertionError("Fixture profile must not be loaded"),
                ) as load_profile,
                patch.object(
                    Oculizer,
                    "_load_controller",
                    side_effect=AssertionError("Enttec must not be initialized"),
                ) as load_controller,
            ):
                controller = Oculizer(
                    None,
                    Mock(),
                    output="qlc-osc",
                    qlc_config_path=config_path,
                )

        self.assertIsNone(controller.dmx_controller)
        self.assertEqual(controller.controller_dict, {})
        self.assertFalse(controller.audio_processing_enabled)
        self.assertIsNone(controller.device_idx)
        get_audio_device.assert_not_called()
        load_profile.assert_not_called()
        load_controller.assert_not_called()
        controller.start()
        self.assertTrue(controller.running.wait(timeout=1.0))
        controller.stop()
        controller.join(timeout=1.0)
        self.assertFalse(controller.is_alive())

    def test_qlc_websocket_dry_run_never_opens_enttec_or_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "qlc_config.json"
            config_path.write_text(json.dumps({
                "websocket": {"dry_run": True},
                "routing": {"scenes": {"party": {"OSCPath": "/party"}}},
            }))
            with (
                patch.object(Oculizer, "_get_audio_device_idx", side_effect=AssertionError("audio")),
                patch.object(Oculizer, "_load_profile", side_effect=AssertionError("profile")),
                patch.object(Oculizer, "_load_controller", side_effect=AssertionError("enttec")),
            ):
                controller = Oculizer(
                    None, Mock(), output="qlc-websocket",
                    qlc_config_path=config_path,
                )

        self.assertEqual(controller.backend.name, "qlc-websocket")
        self.assertFalse(controller.audio_processing_enabled)
        controller.backend.close()


if __name__ == "__main__":
    unittest.main()
