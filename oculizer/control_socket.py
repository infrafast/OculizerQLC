"""Bounded JSON-lines control transport over a local Unix-domain socket."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import threading


MAX_REQUEST_BYTES = 65536


def default_control_socket_path():
    uid = os.getuid() if hasattr(os, "getuid") else os.getpid()
    return f"/tmp/oculizer-{uid}.sock"


class _ControlHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(2.0)
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if not raw:
            return
        if len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
            response = {"ok": False, "error": "request is too large or missing newline"}
        else:
            try:
                request = json.loads(raw.decode("utf-8"))
                result = self.server.runtime_control.handle(request)
                response = {"ok": True, "result": result}
            except Exception as exc:
                response = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
        self.wfile.write(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")


class _ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False


class ControlSocketServer:
    def __init__(self, path, runtime_control):
        self.path = Path(path)
        self.runtime_control = runtime_control
        self.server = None
        self.thread = None
        self.socket_identity = None

    def _remove_stale_socket(self):
        if not self.path.exists():
            return
        if not stat.S_ISSOCK(self.path.lstat().st_mode):
            raise RuntimeError(f"control socket path exists and is not a socket: {self.path}")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.2)
        try:
            probe.connect(str(self.path))
        except OSError:
            self.path.unlink()
        else:
            raise RuntimeError(f"control socket is already active: {self.path}")
        finally:
            probe.close()

    def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_stale_socket()
        self.server = _ThreadingUnixServer(str(self.path), _ControlHandler)
        self.server.runtime_control = self.runtime_control
        os.chmod(self.path, 0o600)
        socket_stat = self.path.stat()
        self.socket_identity = (socket_stat.st_dev, socket_stat.st_ino)
        self.thread = threading.Thread(target=self.server.serve_forever, name="oculizer-control", daemon=True)
        self.thread.start()
        return self

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread is not None:
            self.thread.join(timeout=2.0)
            self.thread = None
        try:
            current = self.path.stat()
            if self.socket_identity == (current.st_dev, current.st_ino):
                self.path.unlink()
        except FileNotFoundError:
            pass
        self.socket_identity = None


def send_control_request(path, request, timeout=2.0):
    payload = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(payload) > MAX_REQUEST_BYTES:
        raise ValueError("request is too large")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
        client.sendall(payload)
        response = b""
        while not response.endswith(b"\n"):
            chunk = client.recv(4096)
            if not chunk:
                break
            response += chunk
            if len(response) > MAX_REQUEST_BYTES:
                raise RuntimeError("control response is too large")
    finally:
        client.close()
    if not response.endswith(b"\n"):
        raise RuntimeError("incomplete control response")
    decoded = json.loads(response.decode("utf-8"))
    if not decoded.get("ok"):
        raise RuntimeError(decoded.get("error", "control command failed"))
    return decoded["result"]
