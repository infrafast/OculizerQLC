# QLC+ 5 Native Network Protocol -- OculizerQLC Integration Notes

## Purpose

This document summarizes the investigation and successful
proof-of-concept for controlling QLC+ 5 through its **native network
protocol** from Python.

The original use case was to let OculizerQLC send **semantic feedback**
to the QLC+ Virtual Console, for example:

-   `OCULIZER: RUNNING`
-   `MODE: AUTO`
-   `PRESET: NORMAL`
-   `BPM: 128`
-   `SCENE: ROCK`

Initially, the QLC+ Web API/WebSocket was considered and a
`setWidgetCaption(widgetId, text)` feature was proposed. A QLC+
developer pointed out that QLC+ 5 already has a **native network
protocol** that is substantially more powerful.

Source-code inspection confirmed that the native protocol exposes
internal QLC+ actions including `VCWidgetCaption`,
`VCWidgetBackgroundColor`, `VCWidgetForegroundColor`, `VCWidgetFont`,
`VCButtonSetPressed`, `VCSliderSetValue`, `FunctionStart`, and
`FunctionStop`.

A Python proof-of-concept was successfully tested: **Python connected to
QLC+ and changed a Virtual Console Label caption to
`HELLO FROM OCULIZER`.**

## 1. Enabling the Native Server

QLC+ 5 supports Web Server and Native Server modes. There is currently
no dedicated `--native` command-line option. Using `--web` forces Web
Server mode, so do **not** use `--web` when the Native Server is
required.

Configure it in the QLC+ GUI:

``` text
Server type: Native server
Start automatically: enabled
Encryption key: configured as desired
```

For the successful proof-of-concept, the GUI encryption-key field
contained `test`. Source inspection indicated that the native
`SimpleCrypt` transport still uses the internal key
`0x5131632B4E33744B`; the test succeeded with the GUI field left at
`test`.

With **Start automatically** enabled, the setting is stored in the
workspace and the Native Server starts when the workspace is loaded.

Native protocol ports:

``` text
UDP 9997  - discovery
TCP 9998  - native connection
```

Check them on Linux/Raspberry Pi with:

``` bash
ss -lunp | grep 9997
ss -ltnp | grep 9998
```

Example QLC+ startup, without `--web`:

``` bash
/usr/bin/qlcplus-qml /path/to/workspace.qxw -f -3 --nowm
```

Technically native server doesn't exclude web server, even though the UI
looks like it.
If you launch QLC+ with the --web option you should still be able to use
the network protocol.


## 2. Protocol Overview

Native packets start with `E6 86`, followed by:

``` text
Protocol ID     2 bytes
Action opcode   2 bytes
Section count   1 byte
Payload length  2 bytes
Payload         variable
```

Important network actions used in the tests:

``` text
0xFF00  NetAnnounce
0xFF01  NetAnnounceReply
0xFF02  NetAuthentication
0xFF03  NetAuthenticationReply
0xFF06  NetProjectTransfer
```

Tested Virtual Console action:

``` text
0xE007  VCWidgetCaption
```

## 3. Example 1 -- Native Server Discovery

Workflow:

``` text
Python
   |
   | UDP :9997 / NetAnnounce
   v
QLC+ Native Server
   |
   | NetAnnounceReply
   v
Python
```

Working discovery prototype:

``` python
#!/usr/bin/env python3

import socket
import struct

QLC_DISCOVERY_PORT = 9997
PROTOCOL_ID = b"\xE6\x86"
NET_ANNOUNCE = 0xFF00
NET_ANNOUNCE_REPLY = 0xFF01
INT_TYPE = 1
STRING_TYPE = 3

def section_int(value: int) -> bytes:
    return bytes([INT_TYPE]) + struct.pack(">I", value & 0xFFFFFFFF)

def section_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return bytes([STRING_TYPE]) + struct.pack(">H", len(data)) + data

def make_packet(opcode: int, *sections: bytes) -> bytes:
    payload = b"".join(sections)
    return (
        PROTOCOL_ID
        + struct.pack(">H", opcode)
        + bytes([len(sections)])
        + struct.pack(">H", len(payload))
        + payload
    )

def decode_packet(packet: bytes):
    if len(packet) < 7:
        raise ValueError("Packet too short")
    if packet[:2] != PROTOCOL_ID:
        raise ValueError("Not a QLC+ native packet")

    opcode = struct.unpack(">H", packet[2:4])[0]
    count = packet[4]
    payload_length = struct.unpack(">H", packet[5:7])[0]
    return opcode, count, packet[7:7 + payload_length]

def discover():
    CLIENT_HOST_TYPE = 2

    packet = make_packet(
        NET_ANNOUNCE,
        section_int(CLIENT_HOST_TYPE),
        section_string("OculizerQLC")
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", QLC_DISCOVERY_PORT))
    sock.settimeout(3)

    print("Sending QLC+ NetAnnounce...")
    sock.sendto(packet, ("255.255.255.255", QLC_DISCOVERY_PORT))
    print("Waiting for QLC+...")

    while True:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            print("No more replies.")
            break

        try:
            opcode, sections, payload = decode_packet(data)
        except ValueError:
            continue

        if opcode == NET_ANNOUNCE_REPLY:
            print("QLC+ server found!")
            print("IP:", addr[0])
            print("opcode: 0x%04X" % opcode)
            print("sections:", sections)
            print("raw payload:", payload.hex(" "))

if __name__ == "__main__":
    discover()
```

This test was successfully executed against QLC+ 5.

## 4. Example 2 -- Dynamic Virtual Console Caption

This test validates the full path:

``` text
Python
   |
   | TCP :9998
   v
QLC+
   |
   | Native authentication
   v
Authenticated client
   |
   | VCWidgetCaption
   v
Virtual Console Label
```

The proof-of-concept successfully changed an existing QLC+ Label to:

``` text
HELLO FROM OCULIZER
```

Complete working Python prototype:

``` python
#!/usr/bin/env python3

import argparse
import os
import socket
import struct
import zlib

QLC_TCP_PORT = 9998
PROTOCOL_ID = b"\xE6\x86"
HEADER_LEN = 7

NET_AUTHENTICATION = 0xFF02
NET_AUTHENTICATION_REPLY = 0xFF03
NET_PROJECT_TRANSFER = 0xFF06
VC_WIDGET_CAPTION = 0xE007

BOOL_TYPE = 0
INT_TYPE = 1
FLOAT_TYPE = 2
STRING_TYPE = 3
BYTEARRAY_TYPE = 4

CRYPT_KEY = 0x5131632B4E33744B
AUTH_KEY_STRING = format(CRYPT_KEY, "x")

CRC_TABLE = [
    0x0000, 0x1081, 0x2102, 0x3183,
    0x4204, 0x5285, 0x6306, 0x7387,
    0x8408, 0x9489, 0xA50A, 0xB58B,
    0xC60C, 0xD68D, 0xE70E, 0xF78F,
]

def qlc_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for c in data:
        crc = ((crc >> 4) & 0x0FFF) ^ CRC_TABLE[(crc ^ c) & 0x0F]
        c >>= 4
        crc = ((crc >> 4) & 0x0FFF) ^ CRC_TABLE[(crc ^ c) & 0x0F]
    return (~crc) & 0xFFFF

def simplecrypt_crc(data: bytes) -> int:
    # Required to reproduce the current QLC+ SimpleCrypt checksum behavior.
    nul_pos = data.find(b"\x00")
    if nul_pos != -1:
        data = data[:nul_pos]
    return qlc_crc16(data)

def crypt_key_parts(key: int):
    return [(key >> (8 * i)) & 0xFF for i in range(8)]

KEY_PARTS = crypt_key_parts(CRYPT_KEY)

def qt_compress(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + zlib.compress(data, 9)

def qt_uncompress(data: bytes) -> bytes:
    expected_size = struct.unpack(">I", data[:4])[0]
    result = zlib.decompress(data[4:])
    if len(result) != expected_size:
        raise ValueError("qUncompress size mismatch")
    return result

def simplecrypt_encrypt(plaintext: bytes) -> bytes:
    flags = 0
    compressed = qt_compress(plaintext)

    if len(compressed) < len(plaintext):
        plaintext = compressed
        flags |= 0x01

    checksum = simplecrypt_crc(plaintext)
    integrity = struct.pack(">H", checksum)
    flags |= 0x02

    data = bytearray(os.urandom(1) + integrity + plaintext)
    last_char = 0

    for pos in range(len(data)):
        encrypted = data[pos] ^ KEY_PARTS[pos % 8] ^ last_char
        data[pos] = encrypted
        last_char = encrypted

    return bytes([0x03, flags]) + bytes(data)

def simplecrypt_decrypt(ciphertext: bytes) -> bytes:
    version = ciphertext[0]
    flags = ciphertext[1]

    if version != 3:
        raise ValueError("Unsupported SimpleCrypt version")

    data = bytearray(ciphertext[2:])
    last_char = 0

    for pos in range(len(data)):
        current_cipher = data[pos]
        data[pos] = current_cipher ^ last_char ^ KEY_PARTS[pos % 8]
        last_char = current_cipher

    data = data[1:]

    if flags & 0x02:
        stored_crc = struct.unpack(">H", data[:2])[0]
        data = data[2:]
        calculated_crc = simplecrypt_crc(bytes(data))
        if stored_crc != calculated_crc:
            raise ValueError(
                f"CRC mismatch: {stored_crc:04x} != {calculated_crc:04x}"
            )

    if flags & 0x01:
        data = bytearray(qt_uncompress(bytes(data)))

    return bytes(data)

def section_int(value: int) -> bytes:
    return bytes([INT_TYPE]) + struct.pack(">I", value & 0xFFFFFFFF)

def section_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return bytes([STRING_TYPE]) + struct.pack(">H", len(data)) + data

def section_bytearray(value: bytes) -> bytes:
    return bytes([BYTEARRAY_TYPE]) + struct.pack(">H", len(value)) + value

def make_plain_packet(opcode: int, *sections: bytes) -> bytes:
    payload = b"".join(sections)
    return (
        PROTOCOL_ID
        + struct.pack(">H", opcode)
        + bytes([len(sections)])
        + struct.pack(">H", len(payload))
        + payload
    )

def encrypt_packet(packet: bytes) -> bytes:
    header = bytearray(packet[:HEADER_LEN])
    plaintext_payload = packet[HEADER_LEN:]
    encrypted_payload = simplecrypt_encrypt(plaintext_payload)
    header[5:7] = struct.pack(">H", len(encrypted_payload))
    return bytes(header) + encrypted_payload

def parse_sections(payload: bytes, count: int):
    result = []
    pos = 0

    for _ in range(count):
        section_type = payload[pos]
        pos += 1

        if section_type == BOOL_TYPE:
            result.append(bool(payload[pos]))
            pos += 1
        elif section_type == INT_TYPE:
            result.append(struct.unpack(">I", payload[pos:pos + 4])[0])
            pos += 4
        elif section_type == STRING_TYPE:
            length = struct.unpack(">H", payload[pos:pos + 2])[0]
            pos += 2
            result.append(
                payload[pos:pos + length].decode("utf-8", errors="replace")
            )
            pos += length
        elif section_type == BYTEARRAY_TYPE:
            length = struct.unpack(">H", payload[pos:pos + 2])[0]
            pos += 2
            result.append(payload[pos:pos + length])
            pos += length
        else:
            raise ValueError(f"Unsupported section type: {section_type}")

    return result

def decode_packet(packet: bytes):
    if packet[:2] != PROTOCOL_ID:
        raise ValueError("Invalid QLC protocol ID")

    opcode = struct.unpack(">H", packet[2:4])[0]
    section_count = packet[4]
    encrypted_length = struct.unpack(">H", packet[5:7])[0]
    encrypted_payload = packet[HEADER_LEN:HEADER_LEN + encrypted_length]

    plaintext_payload = simplecrypt_decrypt(encrypted_payload)
    return opcode, parse_sections(plaintext_payload, section_count)

def recv_exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("QLC+ disconnected")
        data += chunk
    return data

def recv_packet(sock):
    header = recv_exact(sock, HEADER_LEN)
    if header[:2] != PROTOCOL_ID:
        raise ValueError("Invalid QLC packet header")

    payload_length = struct.unpack(">H", header[5:7])[0]
    return header + recv_exact(sock, payload_length)

def authenticate(sock, client_name):
    packet = make_plain_packet(
        NET_AUTHENTICATION,
        section_bytearray(AUTH_KEY_STRING.encode("utf-8")),
        section_string(client_name),
    )

    sock.sendall(encrypt_packet(packet))
    print("Authentication request sent.")

    while True:
        response = recv_packet(sock)
        opcode, sections = decode_packet(response)
        print(f"RX opcode=0x{opcode:04X}", sections)

        if opcode == NET_AUTHENTICATION_REPLY:
            if sections and sections[0] == "Success":
                print("Authentication accepted.")
                return True

            print("Authentication refused.")
            return False

        if opcode == NET_PROJECT_TRANSFER:
            continue

def set_widget_caption(sock, widget_id, text):
    packet = make_plain_packet(
        VC_WIDGET_CAPTION,
        section_int(widget_id),
        section_string(text),
    )
    sock.sendall(encrypt_packet(packet))

def main():
    parser = argparse.ArgumentParser(
        description="QLC+ 5 native VCWidgetCaption test"
    )
    parser.add_argument("host")
    parser.add_argument("widget_id", type=int)
    parser.add_argument("--text", default="HELLO FROM OCULIZER")
    parser.add_argument("--client-name", default="OculizerQLC")
    args = parser.parse_args()

    print(f"Connecting to {args.host}:{QLC_TCP_PORT}...")

    with socket.create_connection(
        (args.host, QLC_TCP_PORT),
        timeout=10
    ) as sock:
        sock.settimeout(60)
        print("TCP connected.")

        if not authenticate(sock, args.client_name):
            return

        set_widget_caption(sock, args.widget_id, args.text)
        print("VCWidgetCaption sent.")
        print("Check the QLC+ Virtual Console.")

if __name__ == "__main__":
    main()
```

Usage:

``` bash
python3 qlc_native_caption.py 127.0.0.1 123
```

or:

``` bash
python3 qlc_native_caption.py 127.0.0.1 123 \
    --text "OCULIZER: RUNNING | AUTO | NORMAL"
```

Replace `123` with the actual Virtual Console widget ID.

## 5. Important SimpleCrypt Detail

The first implementation produced:

``` text
ValueError: CRC mismatch
```

The compatible implementation required this checksum behavior:

``` python
def simplecrypt_crc(data: bytes) -> int:
    nul_pos = data.find(b"\x00")
    if nul_pos != -1:
        data = data[:nul_pos]
    return qlc_crc16(data)
```

This was necessary for successful communication with the tested QLC+ 5
source/version. Do not replace it with a normal CRC over the complete
binary payload without checking the corresponding QLC+ implementation.

## 6. Recommended OculizerQLC Architecture

The proof-of-concept should eventually be refactored into a reusable
native-protocol client:

``` text
Oculizer
    |
    v
QLCNativeClient
    |
    +-- discover()
    +-- connect()
    +-- authenticate()
    +-- set_widget_caption()
    +-- set_widget_background_color()
    +-- set_widget_foreground_color()
    +-- set_slider_value()
    +-- start_function()
    +-- stop_function()
    |
    v
QLC+ Native Server :9998
```

Initial semantic-feedback use case:

``` python
qlc.set_widget_caption(
    STATUS_WIDGET_ID,
    "OCULIZER: RUNNING | AUTO | NORMAL"
)
```

Potential feedback includes service state, AUTO/PAUSE mode,
RESPONSIVE/NORMAL/CALM preset, current scene, BPM, beat state, audio
state, and connection status.

## 7. Why the Native Protocol Matters

The QLC+ Web API is useful for conventional remote control, but the
native protocol exposes a broader set of internal QLC+ actions.

For OculizerQLC, this allows the Virtual Console to become both a
**control interface** and a **semantic status display**, without
artificial state functions or feedback buttons.

## 8. Validated Status

Successfully validated:

-   QLC+ 5 Native Server startup.
-   UDP discovery on port 9997.
-   `NetAnnounce` → `NetAnnounceReply`.
-   TCP connection on port 9998.
-   Native authentication.
-   Python-compatible QLC+ `SimpleCrypt`.
-   `VCWidgetCaption`.
-   Actual QLC+ Virtual Console Label changed to `HELLO FROM OCULIZER`.

**Conclusion:** the QLC+ 5 native network protocol is a proven, viable
integration mechanism for OculizerQLC.
