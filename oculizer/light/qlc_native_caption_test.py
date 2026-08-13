"""Temporary QLC+ native-protocol caption probe for Phase 8a.3.

This deliberately small module is isolated from the lighting backends so it can
be removed without changing their behaviour.
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
import struct
import threading
import time
import zlib


PROTOCOL_ID = b"\xE6\x86"
HEADER_LEN = 7
DEFAULT_KEY = 0x5131632B4E33744B
NET_AUTHENTICATION = 0xFF02
NET_AUTHENTICATION_REPLY = 0xFF03
NET_PROJECT_TRANSFER = 0xFF06
VC_WIDGET_CAPTION = 0xE007
INT_TYPE = 1
STRING_TYPE = 3
BYTEARRAY_TYPE = 4

_CRC_TABLE = (
    0x0000, 0x1081, 0x2102, 0x3183, 0x4204, 0x5285, 0x6306, 0x7387,
    0x8408, 0x9489, 0xA50A, 0xB58B, 0xC60C, 0xD68D, 0xE70E, 0xF78F,
)

logger = logging.getLogger(__name__)


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


class NativeCaptionTest:
    """Authenticate once, then write an incrementing caption every second."""

    def __init__(self, host: str, encryption_key: str = "", widget_id: int = 71):
        self.host = host
        self.encryption_key = encryption_key
        self.widget_id = widget_id
        self.key = _session_key(encryption_key)
        self.socket: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def connect_and_wait_for_authorization(self) -> int:
        logger.info("QLC+ native caption test: connecting to %s:9998", self.host)
        self.socket = socket.create_connection((self.host, 9998), timeout=10)
        self.socket.settimeout(None)
        auth_key = format(self.key, "x").encode("ascii")
        self.socket.sendall(_packet(
            NET_AUTHENTICATION,
            self.key,
            _section_bytearray(auth_key),
            _section_string("OculizerQLC"),
        ))
        logger.warning(
            "QLC+ native caption test: waiting for GUI authorization of "
            "'OculizerQLC' (no timeout)"
        )
        while True:
            header = _recv_exact(self.socket, HEADER_LEN)
            if header[:2] != PROTOCOL_ID:
                raise ValueError("Invalid QLC+ native packet header")
            opcode = struct.unpack(">H", header[2:4])[0]
            count = header[4]
            payload = _recv_exact(self.socket, struct.unpack(">H", header[5:7])[0])
            sections = _parse_sections(_decrypt(payload, self.key), count)
            if opcode == NET_PROJECT_TRANSFER:
                continue
            if opcode != NET_AUTHENTICATION_REPLY:
                continue
            if not sections or sections[0] != "Success":
                raise PermissionError("QLC+ native authorization was refused")
            access_mask = int(sections[1]) if len(sections) > 1 else 0
            logger.info("QLC+ native caption test authorized (access mask: %d)", access_mask)
            return access_mask

    def start_counter(self) -> None:
        if self.socket is None:
            raise RuntimeError("QLC+ native caption test is not connected")
        self._thread = threading.Thread(target=self._counter_loop, daemon=True)
        self._thread.start()

    def _counter_loop(self) -> None:
        counter = 0
        while not self._stop.is_set():
            try:
                assert self.socket is not None
                self.socket.sendall(_packet(
                    VC_WIDGET_CAPTION,
                    self.key,
                    _section_int(self.widget_id),
                    _section_string(str(counter)),
                ))
            except Exception as exc:
                logger.error("QLC+ native caption test stopped: %s", exc)
                return
            counter += 1
            self._stop.wait(1.0)

    def stop(self) -> None:
        self._stop.set()
        if self.socket is not None:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.socket.close()
            self.socket = None
        if self._thread is not None:
            self._thread.join(timeout=2)

