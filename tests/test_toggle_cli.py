import sys
import unittest
from unittest.mock import patch

import toggle


class ToggleCliTests(unittest.TestCase):
    def test_accepts_uppercase_dmx_filter_alias(self):
        with patch.object(
            sys,
            "argv",
            ["toggle.py", "--output", "enttec", "--dmx-dry-run", "--filter-DMX"],
        ):
            args = toggle.parse_args()

        self.assertTrue(args.filter_dmx)

    def test_accepts_multiple_exact_osc_log_filters(self):
        with patch.object(
            sys,
            "argv",
            [
                "toggle.py",
                "--filter-osc",
                "/oculizer/bass",
                "--filter-osc",
                "/oculizer/mid",
            ],
        ):
            args = toggle.parse_args()

        self.assertEqual(args.filter_osc, ["/oculizer/bass", "/oculizer/mid"])

    def test_qlc_mode_does_not_choose_a_fixture_profile(self):
        with (
            patch.object(sys, "argv", ["toggle.py", "--output", "qlc-osc"]),
            patch("toggle.platform.system", return_value="Darwin"),
        ):
            args = toggle.parse_args()

        self.assertIsNone(args.profile)

    def test_enttec_mode_retains_platform_fixture_profile_default(self):
        with (
            patch.object(sys, "argv", ["toggle.py", "--output", "enttec"]),
            patch("toggle.platform.system", return_value="Darwin"),
        ):
            args = toggle.parse_args()

        self.assertEqual(args.profile, "mobile")


if __name__ == "__main__":
    unittest.main()
