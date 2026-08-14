#!/usr/bin/env python3
"""Lightweight HTTP child owned by the headless Oculizer process."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import threading
from urllib.parse import urlparse

from oculizer.control_socket import send_control_request


MAX_BODY_BYTES = 65536
ASSET_DIR = Path(__file__).resolve().parent / "oculizer" / "web"


class OculizerWebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, control_socket):
        super().__init__(address, OculizerWebHandler)
        self.control_socket = str(control_socket)


class OculizerWebHandler(BaseHTTPRequestHandler):
    server_version = "OculizerWeb/1"

    def log_message(self, fmt, *args):
        print(f"Web: {self.address_string()} {fmt % args}", flush=True)

    def _headers_valid(self):
        host = self.headers.get("Host", "")
        if not host or any(character in host for character in "\r\n/\\"):
            return False
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.netloc.casefold() != host.casefold():
                return False
        return True

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, status, value):
        self._send(status, json.dumps(value, separators=(",", ":"), ensure_ascii=False))

    def _socket_for_request(self):
        return self.server.control_socket

    def _request_control(self, request):
        return send_control_request(self._socket_for_request(), request, timeout=2.0)

    def _asset(self, name, content_type):
        try:
            raw = (ASSET_DIR / name).read_bytes()
        except FileNotFoundError:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "asset not found"})
            return
        self._send(HTTPStatus.OK, raw, content_type)

    def do_GET(self):
        if not self._headers_valid():
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid Host or Origin"})
            return
        route = urlparse(self.path).path
        if route == "/":
            self._asset("index.html", "text/html; charset=utf-8")
            return
        if route == "/app.js":
            self._asset("app.js", "text/javascript; charset=utf-8")
            return
        if route == "/style.css":
            self._asset("style.css", "text/css; charset=utf-8")
            return
        commands = {
            "/api/status": {"command": "telemetry"},
            "/api/logs": {"command": "logs", "limit": 50},
            "/api/config/schema": {"command": "config-schema"},
            "/api/config": {"command": "config-get"},
            "/api/dynamic-controls": {"command": "dynamic-controls"},
            "/api/audio-devices": {"command": "audio-devices"},
        }
        request = commands.get(route)
        if request is None:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        try:
            self._json(HTTPStatus.OK, {"ok": True, "result": self._request_control(request)})
        except Exception as exc:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})

    def do_POST(self):
        if not self._headers_valid():
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid Host or Origin"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "invalid request size"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            route = urlparse(self.path).path
            if route == "/api/config/apply":
                request = {
                    "command": "config-apply",
                    "expected_revision": body.get("expected_revision"),
                    "changes": body.get("changes"),
                }
            elif route == "/api/control":
                command = body.get("command")
                if command not in {"auto", "pause", "scene", "dynamic-control"}:
                    raise ValueError("unsupported Web control command")
                request = {key: body[key] for key in ("command", "scene", "name", "expected_revision") if key in body}
            elif route == "/api/restart":
                request = {"command": "restart"}
            else:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
                return
            result = self._request_control(request)
            self._json(HTTPStatus.OK, {"ok": True, "result": result})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._json(HTTPStatus.CONFLICT, {"ok": False, "error": str(exc)})


def parse_args():
    parser = argparse.ArgumentParser(description="Oculizer embedded Web child")
    parser.add_argument("--control-socket", required=True)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    return args


def main():
    args = parse_args()
    server = OculizerWebServer((args.bind, args.port), args.control_socket)
    stopping = threading.Event()

    def stop(_signum=None, _frame=None):
        if stopping.is_set():
            return
        stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"Embedded Web interface listening on http://{args.bind}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
