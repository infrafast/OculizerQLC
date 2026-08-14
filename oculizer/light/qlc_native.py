"""Bounded asynchronous client for the QLC+ 5 native network protocol."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import socket
import struct
import threading
import time
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
STATE_LOG_REPEAT_SECONDS = 30.0
MAX_ENCRYPTED_PAYLOAD = 0xFFFF
MAX_DECRYPTED_PAYLOAD = 1024 * 1024

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
    action_type: str | None = None
    function_id: int | None = None
    slider_mode: str | None = None
    widget_style: str | None = None
    parent_frame_id: int | None = None
    parent_frame_caption: str | None = None
    parent_frame_kind: str | None = None
    frame_path: tuple[str, ...] = ()


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


def _decrypt(ciphertext: bytes, key: int,
             maximum_size: int = MAX_DECRYPTED_PAYLOAD) -> bytes:
    if len(ciphertext) < 3 or ciphertext[0] != 3:
        raise ValueError("Unsupported QLC+ SimpleCrypt payload")
    flags = ciphertext[1]
    if flags & ~0x07:
        raise ValueError(f"Unsupported QLC+ SimpleCrypt flags 0x{flags:02x}")
    if flags & 0x02 and flags & 0x04:
        raise ValueError("Conflicting QLC+ SimpleCrypt integrity flags")
    data = bytearray(ciphertext[2:])
    previous = 0
    parts = _key_parts(key)
    for index, current in enumerate(tuple(data)):
        data[index] = current ^ previous ^ parts[index % 8]
        previous = current
    data = data[1:]
    if flags & 0x02:
        if len(data) < 2:
            raise ValueError("Truncated QLC+ SimpleCrypt CRC")
        expected = struct.unpack(">H", data[:2])[0]
        data = data[2:]
        if _simplecrypt_crc(bytes(data)) != expected:
            raise ValueError("QLC+ SimpleCrypt CRC mismatch")
    elif flags & 0x04:
        if len(data) < 20:
            raise ValueError("Truncated QLC+ SimpleCrypt SHA-1 hash")
        expected = bytes(data[:20])
        data = data[20:]
        if hashlib.sha1(bytes(data)).digest() != expected:
            raise ValueError("QLC+ SimpleCrypt SHA-1 mismatch")
    if flags & 0x01:
        if len(data) < 4:
            raise ValueError("Truncated QLC+ compressed payload size")
        expected_size = struct.unpack(">I", data[:4])[0]
        if expected_size > maximum_size:
            raise ValueError("QLC+ decompressed payload exceeds safe limit")
        decompressor = zlib.decompressobj()
        try:
            data = bytearray(decompressor.decompress(data[4:], maximum_size + 1))
        except zlib.error as exc:
            raise ValueError(f"Invalid QLC+ compressed payload: {exc}") from exc
        if len(data) > maximum_size or decompressor.unconsumed_tail:
            raise ValueError("Invalid or oversized QLC+ compressed payload")
        try:
            data.extend(decompressor.flush(maximum_size - len(data) + 1))
        except zlib.error as exc:
            raise ValueError(f"Invalid QLC+ compressed payload: {exc}") from exc
        if (len(data) > maximum_size or not decompressor.eof
                or decompressor.unused_data):
            raise ValueError("Invalid or oversized QLC+ compressed payload")
        if len(data) != expected_size:
            raise ValueError("QLC+ compressed payload size mismatch")
    elif len(data) > maximum_size:
        raise ValueError("QLC+ decrypted payload exceeds safe limit")
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
    if len(sections) > 0xFF:
        raise ValueError("QLC+ packet has too many sections")
    payload = b"".join(sections)
    encrypted = _encrypt(payload, key)
    if len(encrypted) > MAX_ENCRYPTED_PAYLOAD:
        raise ValueError("QLC+ encrypted packet exceeds protocol limit")
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
        if position >= len(payload):
            raise ValueError("Truncated QLC+ section header")
        kind = payload[position]
        position += 1
        if kind == INT_TYPE:
            if len(payload) - position < 4:
                raise ValueError("Truncated QLC+ integer section")
            result.append(struct.unpack(">I", payload[position:position + 4])[0])
            position += 4
        elif kind == BOOL_TYPE:
            if position >= len(payload):
                raise ValueError("Truncated QLC+ boolean section")
            value = payload[position]
            if value not in (0, 1):
                raise ValueError(f"Invalid QLC+ boolean value {value}")
            result.append(bool(value))
            position += 1
        elif kind in (STRING_TYPE, BYTEARRAY_TYPE):
            if len(payload) - position < 2:
                raise ValueError("Truncated QLC+ variable-length section")
            length = struct.unpack(">H", payload[position:position + 2])[0]
            position += 2
            if len(payload) - position < length:
                raise ValueError("Truncated QLC+ variable-length section payload")
            value = payload[position:position + length]
            position += length
            if kind == STRING_TYPE:
                try:
                    value = value.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError("Invalid UTF-8 in QLC+ string section") from exc
            result.append(value)
        else:
            raise ValueError(f"Unsupported QLC+ section type {kind}")
    if position != len(payload):
        raise ValueError("QLC+ packet contains trailing section data")
    return result


def _parse_section_prefix(payload: bytes, count: int, limit: int) -> list[object]:
    """Decode required leading sections while allowing future trailing fields."""
    if count < limit:
        raise ValueError(
            f"QLC+ packet has {count} sections; at least {limit} are required"
        )
    position = 0
    result = []
    for _ in range(limit):
        # Reuse the strict decoder by locating one complete known section.
        kind = payload[position] if position < len(payload) else None
        if kind == BOOL_TYPE:
            end = position + 2
        elif kind == INT_TYPE:
            end = position + 5
        elif kind in (STRING_TYPE, BYTEARRAY_TYPE):
            if len(payload) - position < 3:
                raise ValueError("Truncated QLC+ variable-length section")
            end = position + 3 + struct.unpack(">H", payload[position + 1:position + 3])[0]
        else:
            raise ValueError(f"Unsupported required QLC+ section type {kind}")
        if end > len(payload):
            raise ValueError("Truncated required QLC+ section")
        result.extend(_parse_sections(payload[position:end], 1))
        position = end
    return result


def _recv_frame(sock: socket.socket, key: int) -> tuple[int, int, bytes]:
    """Read and decrypt exactly one bounded frame without interpreting sections."""
    header = _recv_exact(sock, HEADER_LEN)
    if header[:2] != PROTOCOL_ID:
        raise ValueError("Invalid QLC+ native packet header")
    opcode = struct.unpack(">H", header[2:4])[0]
    count = header[4]
    payload_size = struct.unpack(">H", header[5:7])[0]
    if payload_size < 3:
        raise ValueError("Invalid QLC+ native encrypted payload size")
    encrypted = _recv_exact(sock, payload_size)
    return opcode, count, _decrypt(encrypted, key)


def _recv_packet(sock: socket.socket, key: int) -> tuple[int, list[object]]:
    """Read one frame and strictly decode all its sections."""
    opcode, count, payload = _recv_frame(sock, key)
    return opcode, _parse_sections(payload, count)


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def _attributes(element: ET.Element) -> dict[str, str]:
    return {key.lower(): value for key, value in element.attrib.items()}


def _optional_uint(raw_value: str | None, description: str) -> int | None:
    if raw_value is None or not raw_value.strip():
        return None
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise QLCNativeError(f"Invalid QLC+ {description} {raw_value!r}") from exc
    if not 0 <= value <= 0xFFFFFFFF:
        raise QLCNativeError(f"QLC+ {description} is outside the unsigned 32-bit range")
    return value


def _metadata_uint(raw_value: str | None, description: str) -> int | None:
    """Parse optional evolving metadata without rejecting a usable widget."""
    try:
        value = _optional_uint(raw_value, description)
    except QLCNativeError as exc:
        logger.debug("Ignoring %s", exc)
        return None
    return None if value == 0xFFFFFFFF else value


def parse_project_inventory(xml_data: bytes, maximum_size: int = 16 * 1024 * 1024):
    """Return normalized-caption button/slider inventories from QLC+ XML."""
    from oculizer.light.qlc_websocket import normalize_caption

    if len(xml_data) > maximum_size:
        raise QLCNativeError("QLC+ project exceeds configured native inventory limit")
    lowered = xml_data.lower()
    if b"<!entity" in lowered:
        raise QLCNativeError("QLC+ project XML entities are not allowed")
    declarations = re.findall(br"<!doctype\b[^>]*>", lowered)
    if (b"<!doctype" in lowered and not declarations) or any(
        re.fullmatch(br"<!doctype\s+workspace\s*>", declaration) is None
        for declaration in declarations
    ):
        raise QLCNativeError("QLC+ project external or extended DTD is not allowed")
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        raise QLCNativeError(f"Invalid QLC+ project XML: {exc}") from exc
    inventories = {"button": {}, "slider": {}}
    virtual_consoles = [element for element in root.iter() if _tag(element) == "virtualconsole"]

    def walk(container, parent_frame=None, frame_path=()):
        for element in container:
            kind = _tag(element)
            attrs = _attributes(element)
            if kind in ("frame", "soloframe"):
                frame_caption = attrs.get("caption", "").strip()
                frame = {
                    "id": _metadata_uint(attrs.get("id"), f"{kind} ID"),
                    "caption": frame_caption or None,
                    "kind": kind,
                }
                next_path = frame_path + ((frame_caption,) if frame_caption else ())
                walk(element, frame, next_path)
                continue
            if kind not in inventories:
                # Permit future transparent grouping elements without treating
                # their metadata as a known widget contract.
                walk(element, parent_frame, frame_path)
                continue
            raw_id = attrs.get("id")
            caption = attrs.get("caption", "")
            if raw_id is None or not caption.strip():
                continue
            widget_id = _optional_uint(raw_id, f"{kind} widget ID")
            if widget_id is None:
                raise QLCNativeError(f"QLC+ {kind} widget ID is empty")
            low, high = 0.0, 255.0
            action_type = "toggle" if kind == "button" else None
            function_id = slider_mode = widget_style = None
            if kind == "button":
                for child in element:
                    child_kind = _tag(child)
                    child_attrs = _attributes(child)
                    if child_kind == "action":
                        action_type = (child.text or "").strip().lower() or None
                    elif child_kind == "function":
                        function_id = _metadata_uint(
                            child_attrs.get("id"), f"button {caption!r} function ID",
                        )
            else:
                widget_style = attrs.get("widgetstyle")
                for child in element:
                    child_kind = _tag(child)
                    child_attrs = _attributes(child)
                    if child_kind == "slidermode":
                        slider_mode = (child.text or "").strip().lower() or None
                    elif child_kind == "adjust":
                        function_id = _metadata_uint(
                            child_attrs.get("function"), f"slider {caption!r} function ID",
                        )
                for child in element.iter():
                    child_attrs = _attributes(child)
                    if "lowlimit" in child_attrs and "highlimit" in child_attrs:
                        range_values = child_attrs["lowlimit"], child_attrs["highlimit"]
                    elif "low" in child_attrs and "high" in child_attrs:
                        range_values = child_attrs["low"], child_attrs["high"]
                    else:
                        continue
                    try:
                        low, high = map(float, range_values)
                    except ValueError as exc:
                        raise QLCNativeError(f"Invalid slider range for {caption!r}") from exc
                    break
                if not low < high:
                    raise QLCNativeError(f"Invalid slider range for {caption!r}: {low}..{high}")
            key = normalize_caption(caption)
            if key in inventories[kind]:
                raise QLCNativeError(f"Duplicate QLC+ {kind} caption {caption!r}")
            inventories[kind][key] = NativeWidget(
                widget_id, caption, kind, low, high,
                action_type=action_type,
                function_id=function_id,
                slider_mode=slider_mode,
                widget_style=widget_style,
                parent_frame_id=parent_frame["id"] if parent_frame else None,
                parent_frame_caption=parent_frame["caption"] if parent_frame else None,
                parent_frame_kind=parent_frame["kind"] if parent_frame else None,
                frame_path=frame_path,
            )

    for console in virtual_consoles:
        walk(console)
    return inventories["button"], inventories["slider"]


class QLCNativeClient:
    """Keep one native session alive without blocking Oculizer producers."""

    def __init__(self, host: str, port: int = 9998, encryption_key: str = "",
                 reconnect_seconds: float = 2.0, maximum_project_size: int = 16 * 1024 * 1024,
                 dry_run: bool = False, button_release_seconds: float = 0.1):
        self.host = host
        self.port = port
        self.encryption_key = encryption_key
        self.key = _session_key(encryption_key)
        self.reconnect_seconds = reconnect_seconds
        self.maximum_project_size = maximum_project_size
        self.dry_run = dry_run
        self.button_release_seconds = max(0.0, float(button_release_seconds))
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
        self._state_log_times: dict[NativeState, float] = {}

    def start(self) -> None:
        if self.dry_run:
            self.state = NativeState.READY
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="qlc-native", daemon=True)
        self._thread.start()

    def _set_state(self, state: NativeState) -> bool:
        """Update state and rate-limit repeated reconnect-cycle messages."""
        if state == self.state:
            return False
        self.state = state
        now = time.monotonic()
        last_logged = self._state_log_times.get(state)
        reconnect_cycle_state = state in (
            NativeState.CONNECTING, NativeState.DISCONNECTED,
        )
        if (reconnect_cycle_state and last_logged is not None
                and now - last_logged < STATE_LOG_REPEAT_SECONDS):
            return False
        self._state_log_times[state] = now
        logger.info("QLC+ native state: %s", state.value)
        if state == NativeState.READY:
            # A later outage is a new incident and must be visible immediately,
            # even when the preceding reconnect cycle ended less than 30s ago.
            self._state_log_times.pop(NativeState.CONNECTING, None)
            self._state_log_times.pop(NativeState.DISCONNECTED, None)
            self._last_error = None
        return True

    def _connect(self) -> None:
        if self._set_state(NativeState.CONNECTING):
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
        if self._set_state(NativeState.WAITING_AUTHORIZATION):
            logger.warning("QLC+ native: authorize 'OculizerQLC' in the QLC+ GUI")
        project = bytearray()
        expected_project_size = None
        project_started = False
        while True:
            opcode, section_count, payload = _recv_frame(self.socket, self.key)
            if opcode not in (NET_PROJECT_TRANSFER, NET_AUTHENTICATION_REPLY):
                logger.debug("QLC+ native: ignoring unsupported opcode 0x%04x", opcode)
                continue
            if opcode == NET_PROJECT_TRANSFER:
                self._set_state(NativeState.DOWNLOADING_PROJECT)
                leading = _parse_section_prefix(payload, section_count, 1)
                if not isinstance(leading[0], int):
                    raise QLCNativeError("Invalid QLC+ native project-transfer sequence")
                sequence = int(leading[0])
                if sequence == 0:
                    if project_started:
                        raise QLCNativeError("Invalid QLC+ native project-transfer start")
                    sections = _parse_section_prefix(payload, section_count, 2)
                    if not isinstance(sections[1], int):
                        raise QLCNativeError("Invalid QLC+ native project size")
                    project_started = True
                    expected_project_size = int(sections[1])
                    if expected_project_size > self.maximum_project_size:
                        raise QLCNativeError("QLC+ native project is too large")
                    if expected_project_size:
                        sections = _parse_section_prefix(payload, section_count, 3)
                        if not isinstance(sections[2], bytes):
                            raise QLCNativeError("Invalid QLC+ native project chunk")
                        project.extend(sections[2])
                    if expected_project_size == 0:
                        with self._lock:
                            self.buttons, self.sliders = {}, {}
                        self._set_state(NativeState.READY)
                        return
                elif sequence in (1, 2):
                    sections = _parse_section_prefix(payload, section_count, 2)
                    if not project_started or not isinstance(sections[1], bytes):
                        raise QLCNativeError("Invalid QLC+ native project chunk")
                    project.extend(sections[1])
                else:
                    raise QLCNativeError(f"Invalid QLC+ native project sequence {sequence}")
                if len(project) > self.maximum_project_size:
                    raise QLCNativeError("QLC+ native project is too large")
                if expected_project_size is not None and len(project) > expected_project_size:
                    raise QLCNativeError("QLC+ native project exceeds declared size")
                if sequence == 2 or expected_project_size == len(project):
                    if expected_project_size != len(project):
                        raise QLCNativeError("QLC+ native project ended before declared size")
                    buttons, sliders = parse_project_inventory(bytes(project), self.maximum_project_size)
                    with self._lock:
                        self.buttons, self.sliders = buttons, sliders
                    self._set_state(NativeState.READY)
                    self._flush_pending()
                    return
                continue
            sections = _parse_section_prefix(payload, section_count, 1)
            if not sections or sections[0] != "Success":
                raise QLCNativeError("QLC+ native authorization was refused")
            access_mask = 0
            if section_count > 1:
                sections = _parse_section_prefix(payload, section_count, 2)
                if not isinstance(sections[1], int):
                    raise QLCNativeError("Invalid QLC+ native authorization access mask")
                access_mask = int(sections[1])
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
            key = normalize_caption(caption)
            if self.state == NativeState.READY and key not in self.buttons:
                if key in self.sliders:
                    logger.error(
                        "QLC+ native caption %r resolves to a slider, not a button", caption,
                    )
                else:
                    logger.error("QLC+ native button caption %r is absent", caption)
                return False
            self._pending_scene = caption
        self._outbound.set()
        return True

    def set_slider_level(self, caption: str, value: float) -> bool:
        from oculizer.light.qlc_websocket import normalize_caption
        if self.dry_run:
            logger.info("QLC+ native dry-run: slider '%s' = %.3f", caption, value)
            return True
        with self._lock:
            key = normalize_caption(caption)
            if self.state == NativeState.READY and key not in self.sliders:
                if key in self.buttons:
                    logger.error(
                        "QLC+ native caption %r resolves to a button, not a slider", caption,
                    )
                else:
                    logger.error("QLC+ native slider caption %r is absent", caption)
                return False
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
                    if widget.action_type == "flash":
                        # Flash is the only QLC+ button action whose release has
                        # a distinct required meaning. Toggle and Blackout would
                        # execute again if False were sent after True.
                        if not self._stop.wait(self.button_release_seconds):
                            self.socket.sendall(_packet(
                                VC_BUTTON_SET_PRESSED, self.key,
                                _section_int(widget.widget_id), _section_bool(False),
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
            try:
                self._thread.join(timeout=2)
            except KeyboardInterrupt:
                # Keep direct client users safe too; the interactive entry
                # point suppresses repeated SIGINT for its full cleanup.
                logger.debug("QLC+ native shutdown wait interrupted")
        self._set_state(NativeState.STOPPED)

    close = stop
