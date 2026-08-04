import unittest

from oculizer.rms_graph import RmsGraph, format_elapsed, scene_color_index, scene_visual


class RmsGraphTests(unittest.TestCase):
    def test_sampling_is_rate_limited_and_bounded(self):
        now = [0.0]
        graph = RmsGraph(duration_seconds=1.0, sample_rate_hz=10.0, clock=lambda: now[0])

        self.assertTrue(graph.sample(0.1, "party"))
        now[0] = 0.05
        self.assertFalse(graph.sample(0.2, "party"))
        for index in range(1, 20):
            now[0] = index / 10.0
            graph.sample(index / 100.0, "party")

        self.assertLessEqual(len(graph.samples), 11)

    def test_render_includes_rms_and_elapsed_time_axes(self):
        graph = RmsGraph(duration_seconds=30.0, sample_rate_hz=10.0, clock=lambda: 0.0)
        graph.sample(0.25, "wave", now=0.0)

        lines = graph.render_lines(40, 8, now=5.0)

        self.assertEqual(len(lines), 8)
        self.assertTrue(any(scene_visual("wave").symbol in line for line in lines))
        self.assertEqual(lines[-1].strip(), "00'05\"")
        self.assertTrue(lines[-1].endswith("00'05\""))
        self.assertTrue(lines[0].startswith("1.000"))

    def test_elapsed_time_format_uses_minutes_and_seconds(self):
        self.assertEqual(format_elapsed(0), "00'00\"")
        self.assertEqual(format_elapsed(68), "01'08\"")

    def test_color_word_selects_matching_family_and_dot(self):
        visual = scene_visual("pink_strobe_pulse")

        self.assertEqual(visual.family, "pink")
        self.assertEqual(visual.symbol, "●")
        self.assertTrue(visual.has_named_color)

    def test_scenes_in_same_color_family_get_stable_variations(self):
        first = scene_visual("pink_strobe_pulse")
        second = scene_visual("sequence_pink")

        self.assertEqual(first.family, second.family)
        self.assertNotEqual(first.shade, second.shade)
        self.assertEqual(first, scene_visual("pink_strobe_pulse"))

    def test_scene_without_color_uses_gray_non_dot_icon(self):
        visual = scene_visual("quiet_ambient")

        self.assertEqual(visual.family, "gray")
        self.assertNotEqual(visual.symbol, "●")
        self.assertFalse(visual.has_named_color)

    def test_small_area_is_left_untouched(self):
        graph = RmsGraph(clock=lambda: 0.0)
        self.assertEqual(graph.render_lines(10, 4), [])

    def test_points_retain_the_scene_active_at_sample_time(self):
        graph = RmsGraph(clock=lambda: 0.0)
        graph.sample(0.1, "ambient", now=0.0)
        graph.sample(0.2, "party", now=1.0)

        _lines, points = graph.render_frame(40, 8, now=1.0)

        self.assertEqual({scene for _row, _column, _character, scene in points}, {"ambient", "party"})
        self.assertFalse(any(any(0x2800 <= ord(char) <= 0x28FF for char in line) for line in _lines))
        self.assertEqual(scene_color_index("party"), scene_color_index("party"))

    def test_each_terminal_column_contains_at_most_one_point(self):
        graph = RmsGraph(sample_rate_hz=1000.0, clock=lambda: 0.0)
        graph.sample(0.1, "ambient", now=0.0)
        graph.sample(0.4, "party", now=0.001)

        _lines, points = graph.render_frame(20, 8, now=10.0)
        columns = [column for _row, column, _character, _scene in points]

        self.assertEqual(len(columns), len(set(columns)))

    def test_axes_remain_fixed_as_time_and_signal_level_change(self):
        graph = RmsGraph(duration_seconds=30.0, clock=lambda: 0.0)
        graph.sample(0.01, "ambient", now=0.0)
        early_lines, _early_points = graph.render_frame(40, 8, now=1.0)
        graph.sample(0.9, "party", now=20.0)
        later_lines, _later_points = graph.render_frame(40, 8, now=20.0)

        self.assertTrue(early_lines[0].startswith("1.000"))
        self.assertTrue(later_lines[0].startswith("1.000"))
        self.assertEqual(len(early_lines), len(later_lines))

    def test_shape_is_stable_between_fixed_scroll_boundaries(self):
        graph = RmsGraph(duration_seconds=30.0, clock=lambda: 0.0)
        graph.sample(0.2, "ambient", now=0.0)
        graph.sample(0.8, "party", now=1.0)

        _lines, first_points = graph.render_frame(40, 8, now=2.0)
        _lines, second_points = graph.render_frame(40, 8, now=2.3)

        self.assertEqual(first_points, second_points)

    def test_crossing_a_scroll_boundary_moves_points_one_column_left(self):
        graph = RmsGraph(duration_seconds=30.0, clock=lambda: 0.0)
        graph.sample(0.2, "ambient", now=0.0)

        _lines, before = graph.render_frame(40, 8, now=2.3)
        _lines, after = graph.render_frame(40, 8, now=3.0)

        self.assertEqual(len(before), 1)
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0][1], before[0][1] - 1)
        self.assertEqual(after[0][0], before[0][0])

    def test_braille_cells_connect_adjacent_rms_samples(self):
        graph = RmsGraph(duration_seconds=30.0, clock=lambda: 0.0)
        graph.sample(0.1, "ambient", now=0.0)
        graph.sample(0.8, "party", now=2.0)

        lines = graph.render_lines(40, 8, now=2.0)
        braille_characters = [
            char
            for line in lines
            for char in line
            if 0x2800 <= ord(char) <= 0x28FF
        ]

        self.assertGreater(len(braille_characters), 2)

    def test_steep_rms_change_draws_a_connected_vertical_path(self):
        graph = RmsGraph(duration_seconds=30.0, clock=lambda: 0.0)
        graph.sample(0.0, "ambient", now=0.0)
        graph.sample(1.0, "ambient", now=0.5)

        _lines, cells = graph.render_frame(40, 10, now=0.5)
        occupied_rows = sorted({row for row, _column, _character, _scene in cells})

        self.assertEqual(occupied_rows, list(range(min(occupied_rows), max(occupied_rows) + 1)))

    def test_scene_change_is_overlaid_as_colored_full_marker(self):
        graph = RmsGraph(duration_seconds=30.0, clock=lambda: 0.0)
        graph.sample(0.1, "ambient", now=0.0)
        graph.sample(0.2, "ambient", now=1.0)
        graph.sample(0.3, "party", now=2.0)

        _lines, cells = graph.render_frame(40, 8, now=2.0)
        markers = [
            (character, scene)
            for _row, _column, character, scene in cells
            if character == scene_visual("party").symbol
        ]

        self.assertEqual(markers, [(scene_visual("party").symbol, "party")])

    def test_initial_scene_creates_identity_marker_without_transition_timestamp(self):
        graph = RmsGraph(clock=lambda: 0.0)
        graph.sample(0.1, "ambient", now=0.0)

        lines, cells = graph.render_frame(40, 8, now=0.0)

        markers = [
            (character, scene)
            for _row, _column, character, scene in cells
            if character == scene_visual("ambient").symbol
        ]
        self.assertEqual(markers, [(scene_visual("ambient").symbol, "ambient")])
        self.assertEqual(lines[-1].strip(), "00'00\"")
        self.assertEqual(lines[-1].count("00'00\""), 1)

    def test_scene_transition_timestamp_scrolls_below_marker(self):
        graph = RmsGraph(duration_seconds=30.0, clock=lambda: 0.0)
        graph.sample(0.1, "ambient", now=0.0)
        graph.sample(0.2, "party", now=5.0)

        lines, _cells = graph.render_frame(60, 8, now=10.0)

        self.assertIn("00'05\"", lines[-1])
        self.assertTrue(lines[-1].endswith("00'10\""))

    def test_overlapping_transition_timestamps_keep_the_latest(self):
        graph = RmsGraph(duration_seconds=30.0, sample_rate_hz=10.0, clock=lambda: 0.0)
        graph.sample(0.1, "ambient", now=0.0)
        graph.sample(0.2, "party", now=5.0)
        graph.sample(0.3, "wave", now=6.0)

        lines, _cells = graph.render_frame(40, 8, now=10.0)

        self.assertNotIn("00'05\"", lines[-1])
        self.assertIn("00'06\"", lines[-1])

    def test_transition_timestamp_never_overwrites_fixed_counter(self):
        graph = RmsGraph(duration_seconds=30.0, clock=lambda: 0.0)
        graph.sample(0.1, "ambient", now=0.0)
        graph.sample(0.2, "party", now=29.0)

        lines, _cells = graph.render_frame(40, 8, now=30.0)

        self.assertTrue(lines[-1].endswith("00'30\""))


if __name__ == "__main__":
    unittest.main()
