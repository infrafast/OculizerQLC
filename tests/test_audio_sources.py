import json
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from oculizer.audio.sources import SoundDeviceAudioSource, WavFileAudioSource
from oculizer.light.control import Oculizer
from oculizer.scenes import LogicalSceneRegistry


def write_wav(path: Path, samples, *, sample_rate=8000, channels=1):
    values = np.asarray(samples, dtype="<i2").reshape(-1, channels)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(values.tobytes())


class WavFileAudioSourceTests(unittest.TestCase):
    def test_validates_metadata_and_averages_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stereo.wav"
            write_wav(path, [[32767, -32768], [16384, 16384]], channels=2)
            chunks = []
            source = WavFileAudioSource(path, lambda data, *_: chunks.append(data), 2, loop=False)

            source.start()
            source.join(timeout=1)

        self.assertFalse(source.is_alive())
        self.assertIsNone(source.error)
        self.assertEqual(source.sample_rate, 8000)
        self.assertEqual(source.channels, 2)
        self.assertEqual(chunks[0].shape, (2, 1))
        self.assertAlmostEqual(float(chunks[0][0, 0]), -1.0 / 65536.0, places=6)
        self.assertAlmostEqual(float(chunks[0][1, 0]), 0.5, places=4)

    def test_loops_and_calls_boundary_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "loop.wav"
            write_wav(path, [1000, 2000], sample_rate=48000)
            chunks = []
            loops = []
            source = None

            def callback(data, *_):
                chunks.append(data.copy())
                if len(chunks) == 2:
                    source.stop()

            source = WavFileAudioSource(path, callback, 2, on_loop=lambda: loops.append(True))
            source.start()
            source.join(timeout=1)

        self.assertFalse(source.is_alive())
        self.assertIsNone(source.error)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(len(loops), 1)

    def test_rejects_missing_and_non_wav_files(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            with self.assertRaisesRegex(ValueError, "does not exist"):
                WavFileAudioSource(directory / "missing.wav", lambda *_: None, 1)
            invalid = directory / "invalid.wav"
            invalid.write_text("not audio", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid WAV"):
                WavFileAudioSource(invalid, lambda *_: None, 1)

    def test_source_module_does_not_load_sounddevice(self):
        code = "import oculizer.audio.sources, sys; raise SystemExit('sounddevice' in sys.modules)"
        result = subprocess.run([sys.executable, "-c", code], check=False)
        self.assertEqual(result.returncode, 0)

    def test_oculizer_file_mode_skips_audio_device_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.wav"
            write_wav(path, [1000, 2000])
            qlc_config = Path(directory) / "qlc.json"
            qlc_config.write_text(json.dumps({"lighting": {
                "native": {"dry_run": True},
                "controls": {},
                "routing": {"fallback_scene": "ambient1"},
                "scene_metadata": {"ambient1": {
                    "description": "Ambient", "design_behavior": "normal",
                }},
            }}), encoding="utf-8")
            with (
                patch.object(Oculizer, "_get_audio_device_idx", side_effect=AssertionError("device lookup")),
                patch.object(Oculizer, "_init_scene_prediction"),
            ):
                engine = Oculizer(
                    LogicalSceneRegistry(qlc_config),
                    scene_prediction_enabled=True,
                    config_path=qlc_config,
                    audio_file=path,
                )

            self.assertIsNone(engine.device_idx)
            self.assertEqual(engine.audio_source.path, path.resolve())
            engine.stop()


class SoundDeviceAudioSourceTests(unittest.TestCase):
    def test_adapts_live_stream_to_shared_lifecycle(self):
        stream = Mock()
        stream.active = True
        module = Mock()
        module.InputStream.return_value = stream
        callback = Mock()
        source = SoundDeviceAudioSource(
            device=3,
            channels=2,
            sample_rate=48000,
            block_size=1024,
            callback=callback,
        )

        with patch.dict(sys.modules, {"sounddevice": module}):
            source.start()
            self.assertTrue(source.is_alive())
            source.stop()

        module.InputStream.assert_called_once_with(
            device=3,
            channels=2,
            samplerate=48000,
            blocksize=1024,
            callback=callback,
        )
        stream.start.assert_called_once_with()
        stream.stop.assert_called_once_with()
        stream.close.assert_called_once_with()

    def test_stop_request_does_not_close_stream_from_calling_thread(self):
        stream = Mock()
        stream.active = True
        source = SoundDeviceAudioSource(
            device=3,
            channels=2,
            sample_rate=48000,
            block_size=1024,
            callback=Mock(),
        )
        source.stream = stream

        source.request_stop()

        stream.stop.assert_not_called()
        stream.close.assert_not_called()
        self.assertIs(source.stream, stream)


class OculizerShutdownTests(unittest.TestCase):
    def test_stop_requests_source_shutdown_without_closing_prediction_stream(self):
        engine = object.__new__(Oculizer)
        engine.running = Mock()
        engine.audio_source = Mock()
        engine.prediction_thread = None
        engine.prediction_stream = Mock()
        engine.backend = Mock()

        engine.stop()

        engine.running.clear.assert_called_once_with()
        engine.audio_source.request_stop.assert_called_once_with()
        engine.prediction_stream.stop.assert_not_called()
        engine.prediction_stream.close.assert_not_called()
        engine.backend.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
