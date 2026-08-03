import unittest

from oculizer.modulation import MasterModulator
from oculizer.runtime_config import MasterModulationConfig


class FakeOculizer:
    def __init__(self):
        self.current_audio_rms = None
        self.parameters = []

    def set_parameter(self, name, value):
        self.parameters.append((name, value))
        return True


class MasterModulatorTests(unittest.TestCase):
    def test_normalizes_smooths_rate_limits_and_deduplicates(self):
        engine = FakeOculizer()
        now = [0.0]
        modulator = MasterModulator(
            engine,
            MasterModulationConfig(
                enabled=True,
                rate_hz=25,
                input_floor=0.0,
                input_ceiling=0.1,
                smoothing_factor=0.5,
                change_threshold=0.01,
            ),
            clock=lambda: now[0],
        )

        engine.current_audio_rms = 0.05
        self.assertTrue(modulator.update())
        engine.current_audio_rms = 0.1
        now[0] = 0.02
        self.assertFalse(modulator.update())
        now[0] = 0.04
        self.assertTrue(modulator.update())
        now[0] = 0.08
        engine.current_audio_rms = 0.0755  # Smoothed change is below threshold.
        self.assertFalse(modulator.update())

        self.assertEqual(engine.parameters[0], ("master", 0.5))
        self.assertEqual(engine.parameters[1], ("master", 0.75))

    def test_silence_and_shutdown_send_safe_zero(self):
        engine = FakeOculizer()
        engine.current_audio_rms = 0.0
        modulator = MasterModulator(
            engine,
            MasterModulationConfig(enabled=True, silence_value=0.0, shutdown_value=0.0),
        )

        self.assertTrue(modulator.update())
        self.assertTrue(modulator.shutdown())
        self.assertEqual(engine.parameters, [("master", 0.0), ("master", 0.0)])

    def test_disabled_modulation_sends_nothing(self):
        engine = FakeOculizer()
        engine.current_audio_rms = 0.1
        modulator = MasterModulator(engine, MasterModulationConfig(enabled=False))

        self.assertFalse(modulator.update())
        self.assertFalse(modulator.shutdown())
        self.assertEqual(engine.parameters, [])


if __name__ == "__main__":
    unittest.main()
