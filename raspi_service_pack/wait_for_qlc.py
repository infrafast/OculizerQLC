#!/usr/bin/env python3
"""Bounded readiness check used before the Oculizer service starts."""

import json
from pathlib import Path
import socket
import time


CONFIG = Path("/etc/oculizer/deployment.json")
DEFAULT_TIMEOUT_SECONDS = 30.0


def wait_for_qlc(config, *, timeout_seconds=DEFAULT_TIMEOUT_SECONDS, connector=None):
    """Wait for the configured QLC+ endpoint and report progress visibly."""
    if config["output"] == "qlc-osc":
        print("QLC+ readiness: OSC output selected; waiting 3 seconds for startup...", flush=True)
        time.sleep(3.0)
        print("QLC+ readiness: OSC startup delay complete.", flush=True)
        return True

    connector = connector or socket.create_connection
    host = str(config.get("qlc_host", "127.0.0.1"))
    port = int(config.get("qlc_port", 9999))
    deadline = time.monotonic() + timeout_seconds
    print(
        f"QLC+ readiness: waiting up to {timeout_seconds:g} seconds for "
        f"WebSocket server at {host}:{port}...",
        flush=True,
    )
    while time.monotonic() < deadline:
        try:
            with connector((host, port), timeout=0.5):
                print(f"QLC+ readiness: server available at {host}:{port}.", flush=True)
                return True
        except OSError:
            time.sleep(0.5)
    print(
        f"ERROR: QLC+ WebSocket server did not become available at "
        f"{host}:{port} within {timeout_seconds:g} seconds.\n"
        "Check that QLC+ is running with its Web Server enabled and that "
        "the configured host and port are correct.",
        flush=True,
    )
    return False


def main():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    return 0 if wait_for_qlc(config) else 1


if __name__ == "__main__":
    raise SystemExit(main())
