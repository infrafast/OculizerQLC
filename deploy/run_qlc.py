#!/usr/bin/env python3
"""Start QLC+ from the validated deployment configuration."""

import json
import os
from pathlib import Path


CONFIG = Path("/etc/oculizer/deployment.json")


def build_command(config):
    workspace = Path(config["workspace"])
    if not workspace.is_absolute() or workspace.suffix.lower() != ".qxw":
        raise SystemExit(f"Invalid QLC+ workspace in {CONFIG}: {workspace}")
    if not workspace.is_file():
        raise SystemExit(f"QLC+ workspace is missing: {workspace}")
    return ["/usr/bin/qlcplus-qml", "-w", "-o", str(workspace)]


def main():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    os.environ["QT_QPA_PLATFORM"] = config.get("qlc_platform", "offscreen")
    command = build_command(config)
    os.execv(command[0], ["qlcplus-qml", *command[1:]])


if __name__ == "__main__":
    main()
