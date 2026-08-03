import unittest

from oculizer.modulation import FrequencyBandModulator, MasterModulator
from oculizer.runtime_config import FrequencyBandConfig, FrequencyModulationConfig, MasterModulationConfig


class FakeOculizer:
    def __init__(self):
        self.current_audio_rms = None
        self.current_frequency_bands = None
        self.current_mel_spectrum = None
        self.current_mel_sample_rate = None
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


class FrequencyBandModulatorTests(unittest.TestCase):
    def make_config(self):
        return FrequencyModulationConfig(
            enabled=True,
            rate_hz=25,
            smoothing_factor=0.5,
            change_threshold=0.01,
            bands={
                "bass": FrequencyBandConfig(True, "bass", 20, 250, 0.0, 0.1),
                "mid": FrequencyBandConfig(False, "mid", 250, 2000, 0.0, 0.1),
            },
        )

    def test_sends_only_enabled_normalized_band(self):
        engine = FakeOculizer()
        engine.current_frequency_bands = {"bass": 0.05, "mid": 0.1}
        modulator = FrequencyBandModulator(engine, self.make_config(), clock=lambda: 0.0)

        self.assertTrue(modulator.update())
        self.assertEqual(engine.parameters, [("bass", 0.5)])

    def test_shutdown_sends_safe_value_to_enabled_bands(self):
        engine = FakeOculizer()
        modulator = FrequencyBandModulator(engine, self.make_config())

        self.assertTrue(modulator.shutdown())
        self.assertEqual(engine.parameters, [("bass", 0.0)])

    def test_transient_response_rejects_sustained_band_energy(self):
        engine = FakeOculizer()
        now = [0.0]
        config = FrequencyModulationConfig(
            enabled=True,
            rate_hz=25,
            smoothing_factor=1.0,
            change_threshold=0.0,
            bands={
                "bass": FrequencyBandConfig(
                    True, "bass", 35, 180, 0.001, 0.1, "transient", 0.5
                ),
            },
        )
        modulator = FrequencyBandModulator(engine, config, clock=lambda: now[0])

        engine.current_frequency_bands = {"bass": 0.05}
        self.assertTrue(modulator.update())
        first_value = engine.parameters[-1][1]
        now[0] = 0.04
        self.assertTrue(modulator.update())
        second_value = engine.parameters[-1][1]

        self.assertGreater(first_value, second_value)


if __name__ == "__main__":
    unittest.main()
