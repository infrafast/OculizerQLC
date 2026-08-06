import unittest

import oculizerctl


class OculizerCtlTests(unittest.TestCase):
    def test_builds_scene_and_dynamic_control_requests(self):
        self.assertEqual(
            oculizerctl.build_request(oculizerctl.parse_args(["scene", "wave"])),
            {"command": "scene", "scene": "wave"},
        )
        self.assertEqual(
            oculizerctl.build_request(oculizerctl.parse_args(["dynamic-control", "calm"])),
            {"command": "dynamic-control", "name": "calm"},
        )

        self.assertEqual(
            oculizerctl.build_request(oculizerctl.parse_args(["dynamic-controls"])),
            {"command": "dynamic-controls"},
        )
