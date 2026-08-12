"""QLC+ 5.2.2 WebSocket protocol and Virtual Console discovery."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.request import urlopen


logger = logging.getLogger(__name__)


class QLCWebSocketError(RuntimeError):
    """Raised when QLC+ discovery or WebSocket control fails."""


@dataclass(frozen=True)
class QLCWebSocketConfig:
    host: str = "127.0.0.1"
    port: int = 9999
    path: str = "/qlcplusWS"
    dry_run: bool = False
    connect_timeout_seconds: float = 2.0
    request_timeout_seconds: float = 2.0

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None):
        data = data or {}
        if not isinstance(data, Mapping):
            raise ValueError("QLC+ websocket configuration must be an object")
        config = cls(**{
            name: data.get(name, getattr(cls(), name))
            for name in cls.__dataclass_fields__
        })
        config.validate()
        return config

    def validate(self):
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValueError("QLC+ websocket host must be a non-empty string")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("QLC+ websocket port must be between 1 and 65535")
        if not isinstance(self.path, str) or not self.path.startswith("/"):
            raise ValueError("QLC+ websocket path must start with '/'")
        if not isinstance(self.dry_run, bool):
            raise ValueError("QLC+ websocket dry_run must be a boolean")
        for name in ("connect_timeout_seconds", "request_timeout_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.1 <= value <= 30:
                raise ValueError(f"QLC+ websocket {name} must be between 0.1 and 30")

    @property
    def websocket_url(self):
        return f"ws://{self.host}:{self.port}{self.path}"

    @property
    def inventory_url(self):
        return f"http://{self.host}:{self.port}/vc.json"


@dataclass(frozen=True)
class QLCButton:
    widget_id: int
    caption: str
    action_type: int


@dataclass(frozen=True)
class QLCSlider:
    widget_id: int
    caption: str
    range_low: int
    range_high: int


def normalize_caption(caption: str) -> str:
    """Normalize case and common word separators without fuzzy matching."""
    return "".join(character for character in caption.casefold() if character.isalnum())


def parse_widget_inventory(payload: bytes | str | Mapping[str, Any]):
    """Parse /vc.json and index supported widgets by type and caption."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise QLCWebSocketError(f"Invalid QLC+ Virtual Console JSON: {exc}") from exc
    if not isinstance(payload, Mapping) or not isinstance(payload.get("pages"), list):
        raise QLCWebSocketError("QLC+ Virtual Console JSON must contain a pages array")

    buttons: dict[str, QLCButton] = {}
    sliders: dict[str, QLCSlider] = {}

    def add_unique(index, key, widget):
        if key in index:
            previous = index[key].caption
            raise QLCWebSocketError(
                f"Ambiguous QLC+ widget captions '{previous}' and '{widget.caption}'"
            )
        index[key] = widget

    def visit(widget):
        if not isinstance(widget, Mapping):
            raise QLCWebSocketError("Malformed widget in QLC+ Virtual Console inventory")
        if widget.get("typeId") == 1 or widget.get("type") == "Button":
            caption = widget.get("caption")
            widget_id = widget.get("id")
            action_type = widget.get("actionType")
            if not isinstance(caption, str) or not caption:
                raise QLCWebSocketError("QLC+ button has an empty caption")
            if isinstance(widget_id, bool) or not isinstance(widget_id, int):
                raise QLCWebSocketError(f"QLC+ button '{caption}' has an invalid ID")
            if isinstance(action_type, bool) or not isinstance(action_type, int):
                raise QLCWebSocketError(f"QLC+ button '{caption}' has an invalid actionType")
            key = normalize_caption(caption)
            if not key:
                raise QLCWebSocketError("QLC+ button caption has no letters or digits")
            add_unique(buttons, key, QLCButton(widget_id, caption, action_type))
        elif widget.get("typeId") == 2 or widget.get("type") == "Slider":
            caption = widget.get("caption")
            widget_id = widget.get("id")
            range_low = widget.get("rangeLow")
            range_high = widget.get("rangeHigh")
            if not isinstance(caption, str) or not caption:
                raise QLCWebSocketError("QLC+ slider has an empty caption")
            if isinstance(widget_id, bool) or not isinstance(widget_id, int):
                raise QLCWebSocketError(f"QLC+ slider '{caption}' has an invalid ID")
            if any(isinstance(value, bool) or not isinstance(value, int)
                   for value in (range_low, range_high)):
                raise QLCWebSocketError(f"QLC+ slider '{caption}' has an invalid range")
            if not 0 <= range_low <= range_high <= 255:
                raise QLCWebSocketError(f"QLC+ slider '{caption}' range must be within 0..255")
            key = normalize_caption(caption)
            if not key:
                raise QLCWebSocketError("QLC+ slider caption has no letters or digits")
            add_unique(sliders, key, QLCSlider(widget_id, caption, range_low, range_high))
        children = widget.get("children", [])
        if not isinstance(children, list):
            raise QLCWebSocketError("QLC+ widget children must be an array")
        for child in children:
            visit(child)

    for page in payload["pages"]:
        visit(page)
    return buttons, sliders


def parse_button_inventory(payload):
    """Compatibility helper returning only discovered buttons."""
    return parse_widget_inventory(payload)[0]


class QLCWebSocketClient:
    """Synchronous, bounded client; callers own retry policy."""

    def __init__(self, config: QLCWebSocketConfig, *, websocket_factory=None,
                 inventory_loader: Callable[[], Any] | None = None):
        self.config = config
        self.websocket_factory = websocket_factory
        self.inventory_loader = inventory_loader
        self.socket = None
        self.buttons: dict[str, QLCButton] = {}
        self.sliders: dict[str, QLCSlider] = {}
        self._lock = threading.RLock()
        self._closed = False

    def connect(self):
        if self.config.dry_run:
            return
        with self._lock:
            if self._closed:
                raise QLCWebSocketError("QLC+ WebSocket client is closed")
            try:
                factory = self.websocket_factory
                if factory is None:
                    try:
                        from websocket import create_connection
                    except ImportError as exc:
                        raise QLCWebSocketError(
                            "websocket-client is required for qlc-websocket output"
                        ) from exc
                    factory = create_connection
                self.socket = factory(
                    self.config.websocket_url,
                    timeout=float(self.config.connect_timeout_seconds),
                )
                settimeout = getattr(self.socket, "settimeout", None)
                if settimeout is not None:
                    settimeout(float(self.config.request_timeout_seconds))
                loader = self.inventory_loader or self._load_inventory
                self.buttons, self.sliders = parse_widget_inventory(loader())
            except QLCWebSocketError:
                self._close_socket()
                raise
            except Exception as exc:
                self._close_socket()
                raise QLCWebSocketError(
                    "Cannot connect to QLC+ WebSocket at "
                    f"{self.config.websocket_url} "
                    f"(widget inventory: {self.config.inventory_url}): {exc}"
                ) from exc

    def _load_inventory(self):
        with urlopen(self.config.inventory_url, timeout=self.config.request_timeout_seconds) as response:
            return response.read()

    def validate_captions(self, captions):
        captions = tuple(captions)
        if self.config.dry_run:
            for caption in captions:
                if not isinstance(caption, str) or not caption:
                    raise QLCWebSocketError("QLC+ dry-run caption must be non-empty")
            return
        missing = sorted(
            caption for caption in set(captions)
            if normalize_caption(caption) not in self.buttons
        )
        if missing:
            raise QLCWebSocketError(
                "QLC+ buttons not found by exact caption: " + ", ".join(missing)
            )

    def _request(self, message: str, expected_prefix: str):
        if self.socket is None:
            raise QLCWebSocketError("QLC+ WebSocket is not connected")
        try:
            self.socket.send(message)
            for _ in range(100):
                reply = self.socket.recv()
                if isinstance(reply, bytes):
                    reply = reply.decode("utf-8")
                if isinstance(reply, str) and reply.startswith(expected_prefix):
                    return reply
        except Exception as exc:
            raise QLCWebSocketError(f"QLC+ WebSocket request failed: {exc}") from exc
        raise QLCWebSocketError(f"QLC+ did not reply to '{message}'")

    def activate_button(self, caption: str) -> bool:
        if self.config.dry_run:
            logger.info("QLC+ WebSocket dry-run: activate button caption '%s'", caption)
            return True
        with self._lock:
            button = self.buttons.get(normalize_caption(caption))
            if button is None:
                raise QLCWebSocketError(f"QLC+ button caption '{caption}' is not in the current inventory")
            if button.action_type not in (0, 1, 2, 3):
                raise QLCWebSocketError(
                    f"QLC+ button '{caption}' has unsupported action type {button.action_type}"
                )
            # Flash and Stop All are momentary. Mirror the gestures used by
            # QLC+'s own web UI instead of deriving them from visual state.
            if button.action_type in (1, 3):
                try:
                    self.socket.send(f"{button.widget_id}|255")
                    if button.action_type == 1:
                        self.socket.send(f"{button.widget_id}|0")
                except Exception as exc:
                    raise QLCWebSocketError(
                        f"Cannot activate QLC+ button '{caption}': {exc}"
                    ) from exc
                return True
            prefix = f"QLC+API|getWidgetStatus|{button.widget_id}|"
            reply = self._request(
                f"QLC+API|getWidgetStatus|{button.widget_id}", prefix
            )
            try:
                state = int(reply.split("|")[3])
            except (IndexError, ValueError) as exc:
                raise QLCWebSocketError(f"Malformed QLC+ button status reply: {reply}") from exc
            if state in (127, 255):
                return True
            if state != 0:
                raise QLCWebSocketError(f"Unsupported QLC+ button state {state} for '{caption}'")
            try:
                self.socket.send(f"{button.widget_id}|255")
            except Exception as exc:
                raise QLCWebSocketError(f"Cannot activate QLC+ button '{caption}': {exc}") from exc
            return True

    def set_slider_level(self, caption: str, level: float) -> bool:
        try:
            level = float(level)
        except (TypeError, ValueError) as exc:
            raise QLCWebSocketError("QLC+ slider level must be numeric") from exc
        if level != level or level in (float("inf"), float("-inf")):
            raise QLCWebSocketError("QLC+ slider level must be finite")
        level = max(0.0, min(1.0, level))
        if self.config.dry_run:
            logger.info("QLC+ WebSocket dry-run: slider caption '%s' = %.4f", caption, level)
            return True
        with self._lock:
            slider = self.sliders.get(normalize_caption(caption))
            if slider is None:
                raise QLCWebSocketError(
                    f"QLC+ slider caption '{caption}' is not in the current inventory"
                )
            value = round(slider.range_low + level * (slider.range_high - slider.range_low))
            try:
                self.socket.send(f"{slider.widget_id}|{value}")
            except Exception as exc:
                raise QLCWebSocketError(
                    f"Cannot set QLC+ slider '{caption}': {exc}"
                ) from exc
            return True

    def rediscover(self):
        if self.config.dry_run:
            return
        with self._lock:
            loader = self.inventory_loader or self._load_inventory
            self.buttons, self.sliders = parse_widget_inventory(loader())

    def _close_socket(self):
        socket, self.socket = self.socket, None
        if socket is not None:
            try:
                socket.close()
            except Exception:
                logger.debug("Ignoring QLC+ WebSocket close failure", exc_info=True)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.buttons.clear()
            self.sliders.clear()
            self._close_socket()
