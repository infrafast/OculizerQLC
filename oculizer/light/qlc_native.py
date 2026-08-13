"""Bounded asynchronous client for the QLC+ 5 native network protocol."""

from __future__ import annotations

import hashlib
import logging
import os
import socket
import struct
import threading
import zlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum


PROTOCOL_ID = b"\xE6\x86"
HEADER_LEN = 7
DEFAULT_KEY = 0x5131632B4E33744B
NET_AUTHENTICATION = 0xFF02
NET_AUTHENTICATION_REPLY = 0xFF03
NET_PROJECT_TRANSFER = 0xFF06
VC_WIDGET_CAPTION = 0xE007
VC_BUTTON_SET_PRESSED = 0xF200
VC_SLIDER_SET_VALUE = 0xF300
BOOL_TYPE = 0
INT_TYPE = 1
STRING_TYPE = 3
BYTEARRAY_TYPE = 4

_CRC_TABLE = (
    0x0000, 0x1081, 0x2102, 0x3183, 0x4204, 0x5285, 0x6306, 0x7387,
    0x8408, 0x9489, 0xA50A, 0xB58B, 0xC60C, 0xD68D, 0xE70E, 0xF78F,
)

logger = logging.getLogger(__name__)


class QLCNativeError(RuntimeError):
    pass


class NativeState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    WAITING_AUTHORIZATION = "waiting-for-qlc-authorization"
    DOWNLOADING_PROJECT = "downloading-project"
    READY = "ready"
    STOPPED = "stopped"


@dataclass(frozen=True)
class NativeWidget:
    widget_id: int
    caption: str
    kind: str
    low: float = 0.0
    high: float = 255.0


@dataclass(frozen=True)
class QLCNativeConfig:
    host: str = "127.0.0.1"
    port: int = 9998
    encryption_key: str = ""
    reconnect_seconds: float = 2.0
    maximum_project_size: int = 16 * 1024 * 1024
    dry_run: bool = False

    @classmethod
    def from_mapping(cls, data):
        config = cls(
            host=data.get("host", "127.0.0.1"), port=data.get("port", 9998),
            encryption_key=data.get("encryption_key", ""),
            reconnect_seconds=data.get("reconnect_seconds", 2.0),
            maximum_project_size=data.get("maximum_project_size", 16 * 1024 * 1024),
            dry_run=data.get("dry_run", False),
        )
        config.validate()
        return config

    def validate(self):
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValueError("QLC+ native host must be non-empty")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("QLC+ native port must be between 1 and 65535")
        if not isinstance(self.encryption_key, str):
            raise ValueError("QLC+ native encryption_key must be a string")
        if float(self.reconnect_seconds) < 0.1:
            raise ValueError("QLC+ native reconnect_seconds must be at least 0.1")
        if not 1024 <= int(self.maximum_project_size) <= 128 * 1024 * 1024:
            raise ValueError("QLC+ native maximum_project_size is outside the safe range")


def _session_key(custom_key: str) -> int:
    if not custom_key:
        return DEFAULT_KEY
    digest = hashlib.sha256(custom_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc = ((crc >> 4) & 0x0FFF) ^ _CRC_TABLE[(crc ^ value) & 0x0F]
        value >>= 4
        crc = ((crc >> 4) & 0x0FFF) ^ _CRC_TABLE[(crc ^ value) & 0x0F]
    return (~crc) & 0xFFFF


def _simplecrypt_crc(data: bytes) -> int:
    return _crc16(data.split(b"\x00", 1)[0])


def _key_parts(key: int) -> tuple[int, ...]:
    return tuple((key >> (8 * index)) & 0xFF for index in range(8))


def _encrypt(payload: bytes, key: int) -> bytes:
    flags = 0
    compressed = struct.pack(">I", len(payload)) + zlib.compress(payload, 9)
    if len(compressed) < len(payload):
        payload = compressed
        flags |= 0x01
    payload = struct.pack(">H", _simplecrypt_crc(payload)) + payload
    flags |= 0x02
    data = bytearray(os.urandom(1) + payload)
    previous = 0
    parts = _key_parts(key)
    for index in range(len(data)):
        encrypted = data[index] ^ parts[index % 8] ^ previous
        data[index] = encrypted
        previous = encrypted
    return bytes((3, flags)) + bytes(data)


def _decrypt(ciphertext: bytes, key: int) -> bytes:
    if len(ciphertext) < 2 or ciphertext[0] != 3:
        raise ValueError("Unsupported QLC+ SimpleCrypt payload")
    flags = ciphertext[1]
    data = bytearray(ciphertext[2:])
    previous = 0
    parts = _key_parts(key)
    for index, current in enumerate(tuple(data)):
        data[index] = current ^ previous ^ parts[index % 8]
        previous = current
    data = data[1:]
    if flags & 0x02:
        expected = struct.unpack(">H", data[:2])[0]
        data = data[2:]
        if _simplecrypt_crc(bytes(data)) != expected:
            raise ValueError("QLC+ SimpleCrypt CRC mismatch")
    if flags & 0x01:
        expected_size = struct.unpack(">I", data[:4])[0]
        data = bytearray(zlib.decompress(data[4:]))
        if len(data) != expected_size:
            raise ValueError("QLC+ compressed payload size mismatch")
    return bytes(data)


def _section_int(value: int) -> bytes:
    return bytes((INT_TYPE,)) + struct.pack(">I", value & 0xFFFFFFFF)


def _section_bool(value: bool) -> bytes:
    return bytes((BOOL_TYPE, int(bool(value))))


def _section_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return bytes((STRING_TYPE,)) + struct.pack(">H", len(data)) + data


def _section_bytearray(value: bytes) -> bytes:
    return bytes((BYTEARRAY_TYPE,)) + struct.pack(">H", len(value)) + value


def _packet(opcode: int, key: int, *sections: bytes) -> bytes:
    payload = b"".join(sections)
    encrypted = _encrypt(payload, key)
    return (
        PROTOCOL_ID + struct.pack(">H", opcode) + bytes((len(sections),))
        + struct.pack(">H", len(encrypted)) + encrypted
    )


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = sock.recv(size - len(result))
        if not chunk:
            raise ConnectionError("QLC+ native server disconnected")
        result.extend(chunk)
    return bytes(result)


def _parse_sections(payload: bytes, count: int) -> list[object]:
    result: list[object] = []
    position = 0
    for _ in range(count):
        kind = payload[position]
        position += 1
        if kind == INT_TYPE:
            result.append(struct.unpack(">I", payload[position:position + 4])[0])
            position += 4
        elif kind in (STRING_TYPE, BYTEARRAY_TYPE):
            length = struct.unpack(">H", payload[position:position + 2])[0]
            position += 2
            value = payload[position:position + length]
            position += length
            result.append(value.decode("utf-8", errors="replace") if kind == STRING_TYPE else value)
        else:
            raise ValueError(f"Unsupported QLC+ section type {kind}")
    return result


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def parse_project_inventory(xml_data: bytes, maximum_size: int = 16 * 1024 * 1024):
    """Return normalized-caption button/slider inventories from QLC+ XML."""
    from oculizer.light.qlc_websocket import normalize_caption

    if len(xml_data) > maximum_size:
        raise QLCNativeError("QLC+ project exceeds configured native inventory limit")
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        raise QLCNativeError(f"Invalid QLC+ project XML: {exc}") from exc
    inventories = {"button": {}, "slider": {}}
    for element in root.iter():
        kind = _tag(element)
        if kind not in inventories:
            continue
        raw_id = element.attrib.get("ID", element.attrib.get("id"))
        caption = element.attrib.get("Caption", element.attrib.get("caption", ""))
        if raw_id is None or not caption.strip():
            continue
        try:
            widget_id = int(raw_id)
        except ValueError as exc:
            raise QLCNativeError(f"Invalid QLC+ {kind} widget ID {raw_id!r}") from exc
        low, high = 0.0, 255.0
        if kind == "slider":
            for child in element.iter():
                attrs = {key.lower(): value for key, value in child.attrib.items()}
                if "low" in attrs and "high" in attrs:
                    try:
                        low, high = float(attrs["low"]), float(attrs["high"])
                    except ValueError as exc:
                        raise QLCNativeError(f"Invalid slider range for {caption!r}") from exc
                    break
        key = normalize_caption(caption)
        if key in inventories[kind]:
            raise QLCNativeError(f"Duplicate QLC+ {kind} caption {caption!r}")
        inventories[kind][key] = NativeWidget(widget_id, caption, kind, low, high)
    return inventories["button"], inventories["slider"]


class QLCNativeClient:
    """Keep one native session alive without blocking Oculizer producers."""

    def __init__(self, host: str, port: int = 9998, encryption_key: str = "",
                 reconnect_seconds: float = 2.0, maximum_project_size: int = 16 * 1024 * 1024,
                 dry_run: bool = False):
        self.host = host
        self.port = port
        self.encryption_key = encryption_key
        self.key = _session_key(encryption_key)
        self.reconnect_seconds = reconnect_seconds
        self.maximum_project_size = maximum_project_size
        self.dry_run = dry_run
        self.socket: socket.socket | None = None
        self._stop = threading.Event()
        self._outbound = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.state = NativeState.DISCONNECTED
        self.buttons = {}
        self.sliders = {}
        self._pending_scene: str | None = None
        self._pending_parameters: dict[str, float] = {}
        self._last_error: str | None = None

    def start(self) -> None:
        if self.dry_run:
            self.state = NativeState.READY
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="qlc-native", daemon=True)
        self._thread.start()

    def _set_state(self, state: NativeState) -> None:
        if state != self.state:
            self.state = state
            logger.info("QLC+ native state: %s", state.value)

    def _connect(self) -> None:
        self._set_state(NativeState.CONNECTING)
        logger.info("QLC+ native: connecting to %s:%d", self.host, self.port)
        self.socket = socket.create_connection((self.host, self.port), timeout=10)
        self.socket.settimeout(None)
        auth_key = format(self.key, "x").encode("ascii")
        self.socket.sendall(_packet(
            NET_AUTHENTICATION,
            self.key,
            _section_bytearray(auth_key),
            _section_string("OculizerQLC"),
        ))
        self._set_state(NativeState.WAITING_AUTHORIZATION)
        logger.warning("QLC+ native: authorize 'OculizerQLC' in the QLC+ GUI")
        project = bytearray()
        expected_project_size = None
        while True:
            header = _recv_exact(self.socket, HEADER_LEN)
            if header[:2] != PROTOCOL_ID:
                raise ValueError("Invalid QLC+ native packet header")
            opcode = struct.unpack(">H", header[2:4])[0]
            count = header[4]
            payload = _recv_exact(self.socket, struct.unpack(">H", header[5:7])[0])
            sections = _parse_sections(_decrypt(payload, self.key), count)
            if opcode == NET_PROJECT_TRANSFER:
                self._set_state(NativeState.DOWNLOADING_PROJECT)
                sequence = int(sections[0])
                if sequence == 0:
                    expected_project_size = int(sections[1])
                    if expected_project_size > self.maximum_project_size:
                        raise QLCNativeError("QLC+ native project is too large")
                    if len(sections) > 2:
                        project.extend(sections[2])
                    if expected_project_size == 0:
                        with self._lock:
                            self.buttons, self.sliders = {}, {}
                        self._set_state(NativeState.READY)
                        return
                elif len(sections) > 1:
                    project.extend(sections[1])
                if len(project) > self.maximum_project_size:
                    raise QLCNativeError("QLC+ native project is too large")
                if sequence == 2 or expected_project_size == len(project):
                    buttons, sliders = parse_project_inventory(bytes(project), self.maximum_project_size)
                    with self._lock:
                        self.buttons, self.sliders = buttons, sliders
                    self._set_state(NativeState.READY)
                    self._flush_pending()
                    return
                continue
            if opcode != NET_AUTHENTICATION_REPLY:
                continue
            if not sections or sections[0] != "Success":
                raise QLCNativeError("QLC+ native authorization was refused")
            access_mask = int(sections[1]) if len(sections) > 1 else 0
            logger.info("QLC+ native authorization accepted (access mask: %d)", access_mask)
            if not access_mask & 0x04:
                logger.warning("QLC+ native access mask does not include Virtual Console control")
            self._set_state(NativeState.DOWNLOADING_PROJECT)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._connect()
                while not self._stop.is_set():
                    self._outbound.wait(1.0)
                    self._outbound.clear()
                    self._flush_pending()
            except Exception as exc:
                error = str(exc)
                if error != self._last_error:
                    logger.error("QLC+ native connection unavailable: %s", error)
                    self._last_error = error
            finally:
                self._disconnect()
            if not self._stop.is_set():
                self._set_state(NativeState.DISCONNECTED)
                self._stop.wait(self.reconnect_seconds)

    def activate_button(self, caption: str) -> bool:
        from oculizer.light.qlc_websocket import normalize_caption
        if self.dry_run:
            logger.info("QLC+ native dry-run: activate button caption '%s'", caption)
            return True
        with self._lock:
            self._pending_scene = caption
        self._outbound.set()
        return True

    def set_slider_level(self, caption: str, value: float) -> bool:
        if self.dry_run:
            logger.info("QLC+ native dry-run: slider '%s' = %.3f", caption, value)
            return True
        with self._lock:
            self._pending_parameters[caption] = max(0.0, min(1.0, float(value)))
        self._outbound.set()
        return True

    def _flush_pending(self) -> None:
        from oculizer.light.qlc_websocket import normalize_caption
        with self._lock:
            if self.state != NativeState.READY or self.socket is None:
                return
            scene = self._pending_scene
            parameters = dict(self._pending_parameters)
            if scene is not None:
                widget = self.buttons.get(normalize_caption(scene))
                if widget is None:
                    logger.error("QLC+ native button caption %r is absent", scene)
                else:
                    self.socket.sendall(_packet(
                        VC_BUTTON_SET_PRESSED, self.key,
                        _section_int(widget.widget_id), _section_bool(True),
                    ))
                if self._pending_scene == scene:
                    self._pending_scene = None
            for caption, value in parameters.items():
                widget = self.sliders.get(normalize_caption(caption))
                if widget is None:
                    logger.error("QLC+ native slider caption %r is absent", caption)
                    if self._pending_parameters.get(caption) == value:
                        self._pending_parameters.pop(caption, None)
                    continue
                level = widget.low + value * (widget.high - widget.low)
                self.socket.sendall(_packet(
                    VC_SLIDER_SET_VALUE, self.key,
                    _section_int(widget.widget_id), _section_int(round(level)),
                ))
                if self._pending_parameters.get(caption) == value:
                    self._pending_parameters.pop(caption, None)

    def _disconnect(self) -> None:
        sock, self.socket = self.socket, None
        with self._lock:
            self.buttons, self.sliders = {}, {}
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def stop(self) -> None:
        self._stop.set()
        self._disconnect()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._set_state(NativeState.STOPPED)

    close = stop
