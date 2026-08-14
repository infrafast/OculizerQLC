#!/usr/bin/env python3
"""Command-line client for a running interactive or headless Oculizer."""

import argparse
import json
import os
from pathlib import Path
import socket
import sys

from oculizer.control_socket import default_control_socket_path, send_control_request


DEPLOYMENT_CONFIG = Path("/etc/oculizer/deployment.json")


class ControlSocketDiscoveryError(RuntimeError):
    pass


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Control a running Oculizer instance")
    parser.add_argument(
        "--socket", default=None,
        help="Unix control socket path (default: discover one active runtime)",
    )
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


def control_socket_candidates(*, environ=None, deployment_path=DEPLOYMENT_CONFIG):
    """Return ordered, de-duplicated socket paths from known runtime sources."""
    environ = os.environ if environ is None else environ
    candidates = []

    configured_environment = environ.get("OCULIZER_CONTROL_SOCKET")
    if configured_environment:
        candidates.append(configured_environment)

    try:
        deployment = json.loads(Path(deployment_path).read_text(encoding="utf-8"))
        configured_service = deployment.get("control_socket")
        if isinstance(configured_service, str) and configured_service:
            candidates.append(configured_service)
    except (OSError, ValueError, TypeError):
        pass

    xdg_runtime = environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        uid = os.getuid() if hasattr(os, "getuid") else os.getpid()
        candidates.append(str(Path(xdg_runtime) / f"oculizer-{uid}.sock"))

    candidates.append(default_control_socket_path())
    return tuple(dict.fromkeys(str(Path(path).expanduser()) for path in candidates))


def probe_control_socket(path, timeout=0.2):
    """Return whether a Unix socket currently accepts local connections."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        client.close()


def discover_control_socket(*, candidates=None, probe=probe_control_socket):
    candidates = control_socket_candidates() if candidates is None else tuple(candidates)
    active = tuple(path for path in candidates if probe(path))
    if len(active) == 1:
        return active[0]
    if not active:
        attempted = "\n".join(f"- {path}" for path in candidates)
        raise ControlSocketDiscoveryError(
            "no active Oculizer control socket was found. Paths tried:\n"
            f"{attempted}\nStart the intended runtime or use --socket PATH."
        )
    matches = "\n".join(f"- {path}" for path in active)
    raise ControlSocketDiscoveryError(
        "multiple Oculizer runtimes are active:\n"
        f"{matches}\nUse --socket PATH to select one explicitly."
    )


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
    if args.socket is None:
        try:
            args.socket = discover_control_socket()
        except ControlSocketDiscoveryError as exc:
            print(f"oculizerctl: {exc}", file=sys.stderr)
            return 1
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
