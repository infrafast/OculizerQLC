import unittest

import oculizerctl


class OculizerCtlTests(unittest.TestCase):
    def test_builds_scene_and_preset_requests(self):
        self.assertEqual(
            oculizerctl.build_request(oculizerctl.parse_args(["scene", "wave"])),
            {"command": "scene", "scene": "wave"},
        )
        self.assertEqual(
            oculizerctl.build_request(oculizerctl.parse_args(["preset", "calm"])),
            {"command": "preset", "name": "calm"},
        )

    def test_limits_distinguish_omitted_and_explicit_off(self):
        query = oculizerctl.build_request(oculizerctl.parse_args(["limits"]))
        disable = oculizerctl.build_request(oculizerctl.parse_args(["limits", "--rate", "off"]))

        self.assertEqual(query, {"command": "limits"})
        self.assertEqual(disable, {"command": "limits", "rate": None})

    def test_limits_parse_all_live_values(self):
        request = oculizerctl.build_request(oculizerctl.parse_args([
            "limits", "--cache", "7", "--rate", "6/10", "--throttle", "3/2",
        ]))

        self.assertEqual(request, {
            "command": "limits", "cache": 7, "rate": [6, 10.0], "throttle": [3, 2.0],
        })

