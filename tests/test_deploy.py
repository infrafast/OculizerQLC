import subprocess
import unittest
from unittest.mock import patch

from raspi_service_pack.run_oculizer import build_command as build_oculizer_command
from raspi_service_pack.wait_for_qlc import wait_for_qlc
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def test_installer_shell_syntax_and_help(self):
        subprocess.run(["bash", "-n", str(ROOT / "raspi_service_pack/install.sh")], check=True)
        subprocess.run(["bash", "-n", str(ROOT / "raspi_service_pack/oculizer-service")], check=True)
        result = subprocess.run(
            ["bash", str(ROOT / "raspi_service_pack/install.sh"), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("--check", result.stdout)

    def test_service_command_exposes_manual_and_boot_modes(self):
        result = subprocess.run(
            ["bash", str(ROOT / "raspi_service_pack/oculizer-service")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        for command in ("start", "stop", "restart", "run-auto", "auto", "noauto", "last-state", "health"):
            self.assertIn(command, result.stderr)
        script = (ROOT / "raspi_service_pack/oculizer-service").read_text(encoding="utf-8")
        self.assertNotIn("oculizer-qlc", script)

    def test_installed_control_wrapper_delegates_socket_discovery(self):
        script = (ROOT / "raspi_service_pack/oculizerctl-wrapper").read_text(
            encoding="utf-8",
        )
        self.assertIn('oculizerctl.py" "$@"', script)
        self.assertNotIn('--socket "$socket_path"', script)

    def test_oculizer_command_uses_single_application_configuration(self):
        command = build_oculizer_command({
            "repository": "/opt/Oculizer QLC",
            "audio_input": "default",
            "dynamic_control": "normal",
            "control_socket": "/run/oculizer/control.sock",
        })
        self.assertEqual(command[0], "/opt/Oculizer QLC/.venv/bin/python")
        self.assertNotIn("--output", command)
        self.assertNotIn("--qlc-config", command)
        self.assertIn("/opt/Oculizer QLC/config/oculizer.json", command)
        self.assertIn("/run/oculizer/control.sock", command)

    def test_foreground_run_can_override_system_control_socket(self):
        config = {
            "repository": "/opt/OculizerQLC",
            "audio_input": "default",
            "dynamic_control": "normal",
            "control_socket": "/run/oculizer/control.sock",
        }
        with patch.dict("os.environ", {"OCULIZER_CONTROL_SOCKET": "/tmp/oculizer-1000.sock"}):
            command = build_oculizer_command(config)
        self.assertIn("/tmp/oculizer-1000.sock", command)
        self.assertNotIn("/run/oculizer/control.sock", command)

    def test_qlc_native_readiness_never_blocks_service_startup(self):
        def connector(*_args, **_kwargs):
            self.fail("native readiness must not open a connection")

        with patch("builtins.print") as output:
            self.assertTrue(wait_for_qlc(
                {}, timeout_seconds=1,
                connector=connector,
            ))
        messages = " ".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("startup is asynchronous", messages)


if __name__ == "__main__":
    unittest.main()
