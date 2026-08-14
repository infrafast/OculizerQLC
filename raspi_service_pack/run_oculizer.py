#!/usr/bin/env python3
"""Start headless Oculizer from the validated deployment configuration."""

import json
import os
from pathlib import Path


CONFIG = Path("/etc/oculizer/deployment.json")


def build_command(config):
    repository = Path(config["repository"])
    python = repository / ".venv/bin/python"
    entrypoint = repository / "oculizer_service.py"
    control_socket = os.environ.get(
        "OCULIZER_CONTROL_SOCKET", config["control_socket"]
    )
    command = [
        str(python), str(entrypoint),
        "--config", str(repository / "config/oculizer.json"),
        "--input-device", config["audio_input"],
        "--dynamic-control", config["dynamic_control"],
        "--control-socket", control_socket,
    ]
    if not config.get("web_enabled", True) or os.environ.get("OCULIZER_NO_WEB") == "1":
        command.append("--no-web")
    else:
        command.extend([
            "--web-bind", str(config.get("web_bind", "0.0.0.0")),
            "--web-port", str(config.get("web_port", 8080)),
        ])
    return command


def main():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    args = build_command(config)
    repository = Path(config["repository"])
    python = repository / ".venv/bin/python"
    os.chdir(repository)
    os.execv(str(python), args)


if __name__ == "__main__":
    main()
