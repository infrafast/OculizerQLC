"""Small, dependency-free OSC client for the QLC+ lighting backend."""

from __future__ import annotations

import json
import logging
import socket
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


logger = logging.getLogger(__name__)


class OscConfigError(ValueError):
    """Raised when the QLC+ OSC configuration is invalid."""


def _encode_osc_string(value: str) -> bytes:
    encoded = value.encode("utf-8") + b"\x00"
    return encoded + (b"\x00" * ((-len(encoded)) % 4))


def build_float_message(address: str, value: float) -> bytes:
    """Encode one OSC message containing a single float argument."""
    if not isinstance(address, str) or not address.startswith("/"):
        raise ValueError("OSC address must be a string starting with '/'")
    if "\x00" in address:
        raise ValueError("OSC address must not contain null bytes")

    return (
        _encode_osc_string(address)
        + _encode_osc_string(",f")
        + struct.pack(">f", float(value))
    )


def clamp_level(value: float) -> float:
    """Convert a numeric OSC control value to the supported 0.0–1.0 range."""
    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("OSC value must be numeric") from exc

    if numeric_value != numeric_value or numeric_value in (float("inf"), float("-inf")):
        raise ValueError("OSC value must be finite")
    return max(0.0, min(1.0, numeric_value))


@dataclass(frozen=True)
class OscConfig:
    host: str = "127.0.0.1"
    port: int = 7700
    dry_run: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "OscConfig":
        if not isinstance(data, Mapping):
            raise OscConfigError("OSC configuration must be a JSON object")

        config = cls(
            host=data.get("host", cls.host),
            port=data.get("port", cls.port),
            dry_run=data.get("dry_run", cls.dry_run),
        )
        config.validate()
        return config

    @classmethod
    def from_file(cls, path: str | Path) -> "OscConfig":
        config_path = Path(path).expanduser()
        try:
            with config_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError as exc:
            raise OscConfigError(f"OSC configuration not found: {config_path}") from exc
        except json.JSONDecodeError as exc:
            raise OscConfigError(
                f"Invalid JSON in OSC configuration {config_path}: {exc}"
            ) from exc
        return cls.from_mapping(data)

    def validate(self) -> None:
        if not isinstance(self.host, str) or not self.host.strip():
            raise OscConfigError("OSC host must be a non-empty string")
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise OscConfigError("OSC port must be an integer")
        if not 1 <= self.port <= 65535:
            raise OscConfigError("OSC port must be between 1 and 65535")
        if not isinstance(self.dry_run, bool):
            raise OscConfigError("OSC dry_run must be a boolean")


class OscClient:
    """Thread-safe UDP OSC sender for normalized QLC+ controls."""

    def __init__(self, config: OscConfig, log_filter_paths: Iterable[str] = ()):
        config.validate()
        self.log_filter_paths = frozenset(log_filter_paths)
        for address in self.log_filter_paths:
            try:
                build_float_message(address, 0.0)
            except ValueError as exc:
                raise ValueError(f"Invalid OSC log-filter path: {exc}") from exc
        self.config = config
        self._socket = None if config.dry_run else socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._lock = threading.Lock()
        self._closed = False

    @classmethod
    def from_file(cls, path: str | Path) -> "OscClient":
        return cls(OscConfig.from_file(path))

    @property
    def closed(self) -> bool:
        return self._closed

    def send(self, address: str, value: float) -> bool:
        """Send a normalized float value; return False on a UDP send failure."""
        level = clamp_level(value)
        packet = build_float_message(address, level)

        with self._lock:
            if self._closed:
                raise RuntimeError("OSC client is closed")
            if self.config.dry_run:
                if address not in self.log_filter_paths:
                    logger.info(
                        "OSC dry-run: %s %.4f -> %s:%d",
                        address,
                        level,
                        self.config.host,
                        self.config.port,
                    )
                return True
            try:
                self._socket.sendto(packet, (self.config.host, self.config.port))
                return True
            except OSError as exc:
                logger.error(
                    "Failed to send OSC message %s to %s:%d: %s",
                    address,
                    self.config.host,
                    self.config.port,
                    exc,
                )
                return False

    def press(self, address: str) -> bool:
        return self.send(address, 1.0)

    def release(self, address: str) -> bool:
        return self.send(address, 0.0)

    def set_level(self, address: str, value: float) -> bool:
        return self.send(address, value)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._socket is not None:
                self._socket.close()

    def __enter__(self) -> "OscClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
