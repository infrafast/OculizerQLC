"""Own the lightweight Web child as part of the headless Oculizer lifecycle."""

from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import sys
import time


logger = logging.getLogger(__name__)


class WebChildSupervisor:
    def __init__(self, control_socket, bind="0.0.0.0", port=8080,
                 restart_seconds=2.0, python=None, entrypoint=None, clock=None):
        self.control_socket = str(control_socket)
        self.bind = str(bind)
        self.port = int(port)
        self.restart_seconds = max(0.5, float(restart_seconds))
        self.python = str(python or sys.executable)
        self.entrypoint = str(entrypoint or Path(__file__).resolve().parent.parent / "oculizer_web.py")
        self.clock = clock or time.monotonic
        self.process = None
        self.next_start_at = 0.0
        self.last_check_at = 0.0
        self.stopping = False

    def _command(self):
        return [
            self.python, self.entrypoint,
            "--control-socket", self.control_socket,
            "--bind", self.bind,
            "--port", str(self.port),
        ]

    def start(self):
        self.stopping = False
        self._spawn()

    def _spawn(self):
        if self.process is not None or self.stopping:
            return
        logger.info("Starting embedded Web interface on http://%s:%d", self.bind, self.port)
        self.process = subprocess.Popen(self._command(), close_fds=True)

    def tick(self):
        if self.stopping:
            return
        now = self.clock()
        if now - self.last_check_at < 1.0:
            return
        self.last_check_at = now
        if self.process is not None:
            status = self.process.poll()
            if status is None:
                return
            logger.error("Embedded Web child exited with status %s; retrying in %.1fs",
                         status, self.restart_seconds)
            self.process = None
            self.next_start_at = now + self.restart_seconds
        if self.process is None and now >= self.next_start_at:
            self._spawn()

    def stop(self):
        self.stopping = True
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            logger.warning("Embedded Web child ignored SIGTERM; killing it")
            process.kill()
            process.wait(timeout=2.0)
