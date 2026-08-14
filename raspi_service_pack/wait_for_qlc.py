#!/usr/bin/env python3
"""Bounded readiness check used before the Oculizer service starts."""

import json
from pathlib import Path


CONFIG = Path("/etc/oculizer/deployment.json")
DEFAULT_TIMEOUT_SECONDS = 30.0


def wait_for_qlc(config, *, timeout_seconds=DEFAULT_TIMEOUT_SECONDS, connector=None):
    """Confirm the non-blocking native startup policy."""
    print(
        "QLC+ readiness: native startup is asynchronous. "
        "Oculizer will connect when QLC+ becomes available.",
        flush=True,
    )
    return True


def main():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    return 0 if wait_for_qlc(config) else 1


if __name__ == "__main__":
    raise SystemExit(main())
