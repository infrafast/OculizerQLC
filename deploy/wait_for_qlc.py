#!/usr/bin/env python3
"""Bounded readiness check used before the Oculizer service starts."""

import json
from pathlib import Path
import socket
import time


CONFIG = Path("/etc/oculizer/deployment.json")


def main():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config["output"] == "qlc-osc":
        time.sleep(3.0)
        return
    deadline = time.monotonic() + 30.0
    port = int(config.get("qlc_port", 9999))
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.5)
    raise SystemExit(f"QLC+ did not listen on 127.0.0.1:{port} within 30 seconds")


if __name__ == "__main__":
    main()
