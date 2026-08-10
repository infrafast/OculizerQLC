import subprocess
import tempfile
import unittest
from pathlib import Path

from deploy.run_oculizer import build_command as build_oculizer_command
from deploy.run_qlc import build_command as build_qlc_command


ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def test_installer_shell_syntax_and_help(self):
        subprocess.run(["bash", "-n", str(ROOT / "deploy/install.sh")], check=True)
        result = subprocess.run(
            ["bash", str(ROOT / "deploy/install.sh"), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("--workspace PATH", result.stdout)
        self.assertIn("--check", result.stdout)

    def test_qlc_command_preserves_absolute_workspace_with_spaces(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "Interval show.qxw"
            workspace.touch()
            command = build_qlc_command({"workspace": str(workspace)})
        self.assertEqual(command, ["/usr/bin/qlcplus-qml", "-w", "-o", str(workspace)])

    def test_qlc_command_rejects_relative_workspace(self):
        with self.assertRaises(SystemExit):
            build_qlc_command({"workspace": "qlc/show.qxw"})

    def test_oculizer_command_uses_shared_transport_configuration(self):
        command = build_oculizer_command({
            "repository": "/opt/Oculizer QLC",
            "output": "qlc-websocket",
            "audio_input": "default",
            "dynamic_control": "normal",
            "control_socket": "/run/oculizer/control.sock",
        })
        self.assertEqual(command[0], "/opt/Oculizer QLC/.venv/bin/python")
        self.assertIn("qlc-websocket", command)
        self.assertIn("/run/oculizer/control.sock", command)


if __name__ == "__main__":
    unittest.main()
