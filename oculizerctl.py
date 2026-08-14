#!/usr/bin/env python3
"""Command-line client for a running interactive or headless Oculizer."""

import argparse
import json
import sys

from oculizer.control_socket import default_control_socket_path, send_control_request


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Control a running Oculizer instance")
    parser.add_argument("--socket", default=default_control_socket_path(), help="Unix control socket path")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "auto", "pause", "dynamic-controls"):
        subparsers.add_parser(command)
    scene = subparsers.add_parser("scene")
    scene.add_argument("scene")
    dynamic_control = subparsers.add_parser("dynamic-control")
    dynamic_control.add_argument("name")
    return parser.parse_args(argv)


def build_request(args):
    request = {"command": args.command}
    if args.command == "scene":
        request["scene"] = args.scene
    elif args.command == "dynamic-control":
        request["name"] = args.name
    return request


def control_connection_error(socket_path, exc):
    manual_socket = default_control_socket_path()
    if isinstance(exc, FileNotFoundError):
        reason = "the socket does not exist"
    elif isinstance(exc, ConnectionRefusedError):
        reason = "the socket exists but no Oculizer process is accepting connections"
    elif isinstance(exc, TimeoutError):
        reason = "the Oculizer process did not answer before the timeout"
    else:
        reason = str(exc)
    return (
        f"cannot connect to Oculizer control socket '{socket_path}': {reason}. "
        "Check that the intended runtime is running. The systemd service normally "
        "uses the socket configured in /etc/oculizer/deployment.json; a manual "
        f"run normally uses '{manual_socket}'. Select it explicitly with "
        "--socket PATH."
    )


def main(argv=None):
    args = parse_args(argv)
    request = build_request(args)
    try:
        result = send_control_request(args.socket, request)
    except OSError as exc:
        print(f"oculizerctl: {control_connection_error(args.socket, exc)}", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError) as exc:
        print(f"oculizerctl: control command failed via '{args.socket}': {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
