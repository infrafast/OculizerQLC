import tempfile
import unittest
from pathlib import Path

import numpy as np

from oculizer.runtime_config import SilenceConfig, SpeechConfig
from scripts.render_dynamic_control_comparison import (
    comparison_dynamic_controls,
    count_transitions,
    load_scene_durations,
    render_svg,
    scene_style,
    simulate_profile,
    summarize_simulation,
    xterm_rgb,
)


class DynamicControlComparisonTests(unittest.TestCase):
    def test_scene_durations_come_from_compact_application_metadata(self):
        names, durations = load_scene_durations({"lighting": {"scene_metadata": {
            "ambient1": {"description": "Ambient", "design_behavior": "normal"},
            "strobe": {
                "description": "Strobe",
                "design_behavior": "responsive",
                "max_duration_seconds": 8,
            },
        }}})

        self.assertEqual(names, {"ambient1", "strobe"})
        self.assertEqual(durations, {"strobe": 8.0})

    def test_statistics_report_scene_durations_and_transition_intervals(self):
        summary = summarize_simulation(
            [(0.0, 0.1, None), (1.0, 0.2, "one"),
             (2.0, 0.2, "one"), (3.0, 0.3, "two")],
            4.0,
        )

        self.assertEqual(summary["scene_change_count"], 1)
        self.assertEqual(
            {item["scene"]: item["duration_seconds"]
             for item in summary["scene_statistics"]},
            {"<unrouted>": 1.0, "one": 2.0, "two": 1.0},
        )
        self.assertEqual(summary["transitions"], [
            {"scene": "one", "start_seconds": 1.0,
             "end_seconds": 3.0, "duration_seconds": 2.0},
            {"scene": "two", "start_seconds": 3.0,
             "end_seconds": 4.0, "duration_seconds": 1.0},
        ])

    def test_raw_only_and_explicit_empty_config_skip_dynamic_profiles(self):
        configured = {"control": {"dynamic_controls": {
            "show": {"cache": 3, "rate": None, "throttle": None},
        }}}

        self.assertEqual(comparison_dynamic_controls(configured, raw_only=True), {})
        self.assertEqual(
            comparison_dynamic_controls({"control": {"dynamic_controls": {}}}), {}
        )
        self.assertIn("show", comparison_dynamic_controls(configured))

    def test_scene_styles_reuse_terminal_identity(self):
        self.assertEqual(xterm_rgb(196), "#ff0000")
        self.assertEqual(scene_style("pink_strobe_pulse")[0], "●")
        self.assertNotEqual(scene_style("electric")[0], "●")

    def test_calm_profile_reduces_an_identical_transition_stream(self):
        duration = 30.0
        rms = np.full(301, 0.1)
        predictions = [
            {"time": float(second), "scene": "one" if second % 2 else "two",
             "cluster": 0, "scores": {}}
            for second in range(1, 31)
        ]
        common = dict(
            rms=rms, duration=duration, predictions=predictions, simulation_step=0.1,
            silence_config=SilenceConfig(enabled=False),
            speech_config=SpeechConfig(enabled=False),
            scene_names={"one", "two", "ambient1"}, scene_durations={},
            scene_max_duration=40.0, seed=0,
        )
        raw = simulate_profile(
            "raw (off)", {"cache": 1, "rate": None, "throttle": None}, **common
        )
        calm = simulate_profile(
            "calm", {"cache": 1, "rate": (2, 20.0), "throttle": None}, **common
        )

        self.assertLess(count_transitions(calm), count_transitions(raw))

    def test_svg_names_input_and_every_profile(self):
        samples = [(0.0, 0.1, "one"), (1.0, 0.2, "two")]
        profiles = [
            ("raw (off)", {"cache": 10, "rate": None, "throttle": None}),
            ("show", {"cache": 3, "rate": (4, 10.0), "throttle": None}),
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "comparison.svg"
            render_svg(
                output, Path("concert.wav"), "v6", 1.0, profiles,
                {"raw (off)": samples, "show": samples}, 1.0, 800,
            )
            rendered = output.read_text(encoding="utf-8")

        self.assertIn("concert.wav", rendered)
        self.assertIn(">raw (off)</text>", rendered)
        self.assertIn(">show</text>", rendered)


if __name__ == "__main__":
    unittest.main()
