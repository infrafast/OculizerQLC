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


def main(argv=None):
    args = parse_args(argv)
    request = build_request(args)
    try:
        result = send_control_request(args.socket, request)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"oculizerctl: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
