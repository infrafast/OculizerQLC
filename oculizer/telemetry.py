"""Small bounded telemetry primitives shared by terminal, control, and Web UIs."""

from __future__ import annotations

from collections import deque
import logging
import re
import threading


class BoundedLogHandler(logging.Handler):
    """Retain a small formatted log tail without reading files or journald."""

    def __init__(self, capacity=200):
        super().__init__()
        if not 10 <= int(capacity) <= 2000:
            raise ValueError("log capacity must be between 10 and 2000")
        self.records = deque(maxlen=int(capacity))
        self.records_lock = threading.Lock()
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record):
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        message = re.sub(
            r"(?i)(encryption[_ -]?key|password|token)(\s*[=:]\s*|\s+)([^\s,;]+)",
            r"\1\2[redacted]",
            message,
        )
        with self.records_lock:
            self.records.append(message[:1024])

    def tail(self, limit=100):
        limit = max(1, min(int(limit), self.records.maxlen))
        with self.records_lock:
            return list(self.records)[-limit:]
