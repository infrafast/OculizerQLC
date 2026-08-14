import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

import oculizerctl


class OculizerCtlTests(unittest.TestCase):
    def test_discovers_one_active_socket_from_known_candidates(self):
        candidates = (
            "/run/oculizer/control.sock",
            "/tmp/oculizer-1000.sock",
        )
        selected = oculizerctl.discover_control_socket(
            candidates=candidates,
            probe=lambda path: path.endswith("1000.sock"),
        )
        self.assertEqual(selected, "/tmp/oculizer-1000.sock")

    def test_discovery_rejects_zero_or_multiple_active_sockets(self):
        candidates = ("/run/oculizer/control.sock", "/tmp/oculizer-1000.sock")
        with self.assertRaisesRegex(
            oculizerctl.ControlSocketDiscoveryError, "no active.*Paths tried",
        ):
            oculizerctl.discover_control_socket(
                candidates=candidates, probe=lambda _path: False,
            )
        with self.assertRaisesRegex(
            oculizerctl.ControlSocketDiscoveryError, "multiple Oculizer runtimes",
        ):
            oculizerctl.discover_control_socket(
                candidates=candidates, probe=lambda _path: True,
            )

    def test_candidate_order_includes_environment_deployment_xdg_and_tmp(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            deployment = Path(directory) / "deployment.json"
            deployment.write_text(
                '{"control_socket": "/run/oculizer/control.sock"}',
                encoding="utf-8",
            )
            candidates = oculizerctl.control_socket_candidates(
                environ={
                    "OCULIZER_CONTROL_SOCKET": "/custom/control.sock",
                    "XDG_RUNTIME_DIR": "/run/user/1000",
                },
                deployment_path=deployment,
            )

        self.assertEqual(candidates[0], "/custom/control.sock")
        self.assertEqual(candidates[1], "/run/oculizer/control.sock")
        self.assertIn("/run/user/1000/oculizer-", candidates[2])
        self.assertEqual(candidates[-1], oculizerctl.default_control_socket_path())

    def test_main_auto_discovers_socket_before_sending_request(self):
        with patch(
            "oculizerctl.discover_control_socket", return_value="/tmp/active.sock",
        ), patch(
            "oculizerctl.send_control_request", return_value={"mode": "auto"},
        ) as request:
            self.assertEqual(oculizerctl.main(["status"]), 0)
        request.assert_called_once_with("/tmp/active.sock", {"command": "status"})

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
