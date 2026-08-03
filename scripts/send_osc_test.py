#!/usr/bin/env python3
"""Send a minimal OSC float message for the QLC+ milestone-0 test."""

from __future__ import annotations

import argparse
import socket
import struct
import time


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7700
DEFAULT_ADDRESS = "/test"


def _encode_osc_string(value: str) -> bytes:
    encoded = value.encode("utf-8") + b"\x00"
    return encoded + (b"\x00" * ((-len(encoded)) % 4))


def build_float_message(address: str, value: float) -> bytes:
    """Build one self-contained OSC float message for milestone testing."""
    if not isinstance(address, str) or not address.startswith("/"):
        raise ValueError("OSC address must be a string starting with '/'")
    if "\x00" in address:
        raise ValueError("OSC address must not contain null bytes")
    return (
        _encode_osc_string(address)
        + _encode_osc_string(",f")
        + struct.pack(">f", float(value))
    )


def send_float(host: str, port: int, address: str, value: float) -> None:
    """Send one OSC float message over UDP."""
    packet = build_float_message(address, value)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.sendto(packet, (host, port))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send the milestone-0 OSC test message to QLC+ 5."
    )
    parser.add_argument("value", nargs="?", type=float, default=1.0)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    parser.add_argument(
        "--pulse",
        type=float,
        metavar="SECONDS",
        help="Send 1.0, wait for the given duration, then send 0.0.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("Port must be between 1 and 65535")
    if args.pulse is not None and args.pulse < 0:
        raise SystemExit("Pulse duration must be zero or greater")

    if args.pulse is None:
        send_float(args.host, args.port, args.address, args.value)
        print(f"Sent {args.address} {args.value:g} to {args.host}:{args.port}")
        return

    send_float(args.host, args.port, args.address, 1.0)
    print(f"Sent {args.address} 1 to {args.host}:{args.port}")
    time.sleep(args.pulse)
    send_float(args.host, args.port, args.address, 0.0)
    print(f"Sent {args.address} 0 to {args.host}:{args.port}")


if __name__ == "__main__":
    main()
