import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oculizer.light.control import Oculizer
from oculizer.runtime_config import configured_audio_input, load_runtime_config


class RuntimeConfigTests(unittest.TestCase):
    def test_loads_audio_input_and_defaults_to_os_device(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oculizer.json"
            path.write_text(json.dumps({"audio": {"input_device": "BlackHole"}}))
            self.assertEqual(configured_audio_input(load_runtime_config(path)), "BlackHole")

            path.write_text("{}")
            self.assertEqual(configured_audio_input(load_runtime_config(path)), "default")

    def test_rejects_invalid_audio_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oculizer.json"
            path.write_text(json.dumps({"audio": {"input_device": True}}))
            with self.assertRaisesRegex(ValueError, "input_device"):
                load_runtime_config(path)


class AudioDeviceResolutionTests(unittest.TestCase):
    def make_controller(self, selector):
        controller = object.__new__(Oculizer)
        controller.input_device = str(selector)
        return controller

    def test_uses_host_api_default_input(self):
        devices = [
            {"name": "System microphone", "max_input_channels": 1},
            {"name": "Speakers", "max_input_channels": 0},
        ]
        with patch("oculizer.light.control.sd.query_devices") as query_devices:
            query_devices.side_effect = [devices, {"index": 0}]
            self.assertEqual(self.make_controller("default")._get_audio_device_idx(), 0)

    def test_accepts_alias_name_and_index(self):
        devices = [
            {"name": "Microphone", "max_input_channels": 1},
            {"name": "BlackHole 2ch", "max_input_channels": 2},
        ]
        for selector in ("blackhole", "BlackHole 2ch", "1"):
            with self.subTest(selector=selector), patch(
                "oculizer.light.control.sd.query_devices", return_value=devices
            ):
                self.assertEqual(self.make_controller(selector)._get_audio_device_idx(), 1)


if __name__ == "__main__":
    unittest.main()
