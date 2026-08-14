import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

import oculizerctl


class OculizerCtlTests(unittest.TestCase):
    def test_missing_socket_error_identifies_path_and_manual_alternative(self):
        stderr = io.StringIO()
        with patch(
            "oculizerctl.send_control_request",
            side_effect=FileNotFoundError(2, "No such file or directory"),
        ), redirect_stderr(stderr):
            result = oculizerctl.main([
                "--socket", "/run/oculizer/control.sock", "status",
            ])

        message = stderr.getvalue()
        self.assertEqual(result, 1)
        self.assertIn("cannot connect to Oculizer control socket '/run/oculizer/control.sock'", message)
        self.assertIn("the socket does not exist", message)
        self.assertIn("/tmp/oculizer-", message)
        self.assertIn("--socket PATH", message)

    def test_refused_socket_error_distinguishes_stale_listener(self):
        stderr = io.StringIO()
        with patch(
            "oculizerctl.send_control_request",
            side_effect=ConnectionRefusedError(61, "Connection refused"),
        ), redirect_stderr(stderr):
            result = oculizerctl.main([
                "--socket", "/tmp/oculizer-test.sock", "status",
            ])

        self.assertEqual(result, 1)
        self.assertIn("exists but no Oculizer process", stderr.getvalue())

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
