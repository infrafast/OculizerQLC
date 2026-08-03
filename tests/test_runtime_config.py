import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oculizer.light.control import Oculizer
from oculizer.runtime_config import configured_audio_input, configured_prediction, configured_silence, load_runtime_config


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

    def test_loads_configurable_silence_scene_and_hysteresis(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oculizer.json"
            path.write_text(
                json.dumps(
                    {
                        "audio": {
                            "silence": {
                                "threshold": 0.01,
                                "resume_threshold": 0.02,
                                "duration_seconds": 3,
                                "scene": "ambient1",
                            }
                        }
                    }
                )
            )
            config = load_runtime_config(path)

        silence = configured_silence(config)
        self.assertEqual(silence.scene, "ambient1")
        self.assertEqual(silence.threshold, 0.01)
        self.assertEqual(silence.resume_threshold, 0.02)
        self.assertEqual(silence.duration_seconds, 3.0)

    def test_rejects_invalid_silence_hysteresis(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oculizer.json"
            path.write_text(
                json.dumps(
                    {
                        "audio": {
                            "silence": {
                                "threshold": 0.02,
                                "resume_threshold": 0.01,
                            }
                        }
                    }
                )
            )
            with self.assertRaisesRegex(ValueError, "resume_threshold"):
                load_runtime_config(path)

    def test_loads_prediction_window(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oculizer.json"
            path.write_text(json.dumps({"audio": {"prediction": {"window_seconds": 2.5}}}))
            config = load_runtime_config(path)
        self.assertEqual(configured_prediction(config).window_seconds, 2.5)


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
