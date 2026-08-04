#!/usr/bin/env python3
"""Command-line client for a running interactive or headless Oculizer."""

import argparse
import json
import sys

from oculizer.control_socket import default_control_socket_path, send_control_request


def parse_policy(value):
    if value.casefold() == "off":
        return None
    try:
        count_text, seconds_text = value.split("/", 1)
        count, seconds = int(count_text), float(seconds_text)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("expected COUNT/SECONDS or off") from exc
    if not 1 <= count <= 100 or not 0.5 <= seconds <= 300:
        raise argparse.ArgumentTypeError("count must be 1-100 and seconds 0.5-300")
    return [count, seconds]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Control a running Oculizer instance")
    parser.add_argument("--socket", default=default_control_socket_path(), help="Unix control socket path")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "auto", "pause", "presets"):
        subparsers.add_parser(command)
    scene = subparsers.add_parser("scene")
    scene.add_argument("scene")
    preset = subparsers.add_parser("preset")
    preset.add_argument("name")
    limits = subparsers.add_parser("limits")
    limits.add_argument("--cache", type=int, default=argparse.SUPPRESS)
    limits.add_argument("--rate", type=parse_policy, default=argparse.SUPPRESS)
    limits.add_argument("--throttle", type=parse_policy, default=argparse.SUPPRESS)
    return parser.parse_args(argv)


def build_request(args):
    request = {"command": args.command}
    if args.command == "scene":
        request["scene"] = args.scene
    elif args.command == "preset":
        request["name"] = args.name
    elif args.command == "limits":
        for key in ("cache", "rate", "throttle"):
            if hasattr(args, key):
                request[key] = getattr(args, key)
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
