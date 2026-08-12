# QLC+ 5 Network Protocol Specifications

*Specification of the network protocol used by the QLC+ 5 "Tardis" subsystem.*

This document describes the protocol **as implemented** in `qmlui/tardis/`
(`networkpacketizer.cpp`, `networkmanager.cpp`, `tardis.cpp`, `simplecrypt.cpp`).
Where the implementation diverges from the original design intent, the divergence is
called out explicitly rather than papered over.

---

## 1. Overview

The QLC+ 5 protocol is a network based, session oriented protocol that allows several
QLC+ instances to share a single workspace and stay synchronised in real time.

Two roles exist:

| Role | Description |
| --- | --- |
| **Server** | Owns the authoritative workspace. Accepts client connections, authenticates them, transfers the project and broadcasts changes. |
| **Client** | Discovers servers, authenticates, downloads the project and exchanges changes with the server. |

The protocol is built on the *Tardis* action model. Every user operation in QLC+ 5
(creating a fixture, moving a widget, pressing a VC button…) is encoded as a `TardisAction`,
which is used both for local undo/redo history and, when a network session is active,
for replication to the other peers. This means that the set of network messages is
essentially *the set of QLC+ action codes* — the protocol does not define a separate
command vocabulary for editing operations.

Transport usage:

| Transport | Port | Usage |
| --- | --- | --- |
| **UDP** (broadcast/unicast) | 9997 | Host discovery (announce / announce reply) |
| **TCP** | 9998 | Authentication, project transfer, action replication |

Both ports are compile-time constants (`DEFAULT_UDP_PORT`, `DEFAULT_TCP_PORT` in
`networkmanager.cpp`) and are currently not configurable at runtime.

> **Note on the Web server mode.** `NetworkManager` can also run the QLC+ instance as a
> *Web server* (`WebAccessQml`), which serves the HTTP/WebSocket interface instead of
> the native protocol described here. The two modes are mutually exclusive: when
> `serverType` is `WebServer`, none of the packets in this document are exchanged.

---

## 2. Packet structure

Every packet — UDP or TCP — begins with a fixed 7 byte header, followed by a variable
number of *sections* carrying the payload. All multi-byte integer fields are transmitted
**MSB first (big endian)**, with the exception of floating point values (see §3).

### Table A — Packet bitstream

| Field # | Field name | Size | Description |
| --- | --- | --- | --- |
| 1 | Protocol ID | `u16` | Fixed value `0xE686`. Also acts as the protocol version. |
| 2 | Message code | `u16` | Unique code identifying an action (MSB first) |
| 3 | Sections number | `u8` | The number of sections following the header |
| 4 | Sections length | `u16` | Total length in bytes of all the sections that follow |
| 5 | Section type | `u8` | The type of the section (see Table B) |
| 6 | Section size *(optional)* | `u16` | Size of the section data, only for variable length types |
| 7 | Section data | `u8[1 - 65535]` | The section payload |

Fields #5, #6 and #7 are repeated *Sections number* times.

The header is defined by `HEADER_LENGTH = 7` and is built by
`NetworkPacketizer::initializePacket()`. Fields #3 and #4 are back-patched by
`NetworkPacketizer::addSection()` every time a section is appended.

The `0xE686` protocol ID is a synchronisation marker: a receiver that loses byte
alignment on the TCP stream can scan forward for it to re-synchronise. The decoder
rejects any packet whose first two bytes do not match.

### Packet framing on TCP

TCP is a stream, so several packets may arrive coalesced in a single `readyRead()`, and a
packet may arrive split across reads. `NetworkPacketizer::decodePacket()` handles this
with its return value:

| Return value | Meaning |
| --- | --- |
| `> 0` | Number of bytes consumed. The caller advances by this amount and keeps decoding. |
| `-1` | Incomplete packet: fewer than `HEADER_LENGTH + sections_length` bytes available. The caller must read more data from the socket and retry. |
| `1` (with no valid header) | Not a QLC+ packet — protocol ID missing or buffer shorter than the header. |

On a malformed section (unknown type, or a length field pointing past the end of the
payload), the decoder logs a warning, clears the section list, sets the opCode to `-1`
and returns `HEADER_LENGTH + sections_length` so the caller can skip the whole bad packet
and resynchronise on the next one.

---

## 3. Section types

Each section is `[type:u8][optional length:u16][data]`. The type values are defined by
the anonymous enum in `networkpacketizer.h` and are **positional**: the numeric value is
the enum index, so section types must only ever be appended at the end of the enum,
never reordered or removed.

### Table B — Section types

| Value | Name | Length field | Data layout |
| --- | --- | --- | --- |
| 0 | `BoolType` | no | `u8` — `0x00` false, `0x01` true |
| 1 | `IntType` | no | `i32`, MSB first. Used for both `Int` and `UInt` QVariants |
| 2 | `FloatType` | no | 4 bytes, native `float` memory layout |
| 3 | `StringType` | yes | UTF-8 encoded string, no NUL terminator |
| 4 | `ByteArrayType` | yes | raw bytes |
| 5 | `Vector3DType` | no | 3 × `float` (x, y, z), native layout |
| 6 | `RectFType` | no | 4 × `float` (x, y, w, h), native layout |
| 7 | `ColorType` | no | `u32` `QRgb` (`0xAARRGGBB`), MSB first |
| 8 | `FontType` | yes | UTF-8 string produced by `QFont::toString()` |
| 9 | `SceneValueType` | no | `u32` fixture ID + `u16` channel + `u8` value = 7 bytes |
| 10 | `UIntPairType` | no | 2 × `u32`, MSB first = 8 bytes |
| 11 | `StringIntPairType` | yes* | `u16` length + UTF-8 string, then `i32` MSB first |
| 12 | `StringDoublePairType` | yes* | `u16` length + UTF-8 string, then 8 byte native `double` |
| 13 | `StringStringPairType` | yes* | two consecutive (`u16` length + UTF-8 string) pairs |
| 14 | `PointFType` | no | 2 × `float` (x, y), native layout |

\* the length field belongs to the embedded string(s), not to the section as a whole.

#### Endianness caveat

Byte order is **not uniform across section types**:

| Byte order | Section types | Mechanism |
| --- | --- | --- |
| Big endian (MSB first) | `IntType`, `ColorType`, `SceneValueType`, `UIntPairType`, the `int` half of `StringIntPairType`, all `u16` length fields | Explicit per-byte shifting |
| **Native (not swapped)** | `FloatType`, `Vector3DType`, `RectFType`, `PointFType`, the `double` half of `StringDoublePairType` | `reinterpret_cast` over host memory |

Floating point sections are copied verbatim from host memory on encode and decode. This is
interoperable between hosts of the same endianness (and every mainstream QLC+ target is
little endian), but it is *not* architecture neutral — a big endian peer would misread all
float payloads. Implementers of third party clients should assume **little endian
IEEE-754** for these fields.

#### Special cases in `addSection()`

Two QVariant inputs do not map one-to-one onto a section:

| Input | Emitted as | Notes |
| --- | --- | --- |
| `QVariantList` | N consecutive sections, one per item | Not a section type of its own — the list is flattened. Only items convertible to `SceneValue` are accepted; anything else is skipped with a warning. The receiver reassembles the list from the trailing sections (see §5.2). |
| Null `QVariant` | *nothing* | `addSection()` returns immediately. A packet may legitimately carry fewer sections than the sender's action had values. |

---

## 4. Message codes

The message code (field #2) is a `Tardis::ActionCodes` value. Codes are grouped by
subsystem:

| Base | Codes in use | Group | Enters undo history |
| --- | --- | --- | --- |
| `0x0000` | `0x0000` – `0x0006` | Preview / environment settings | Yes |
| `0x0090` | `0x0090` – `0x0091` | Input/Output universe management | Yes |
| `0x0100` | `0x0100` – `0x0106` | Fixture and fixture group editing | Yes |
| `0x0200` | `0x0200` – `0x0240` | Function editing (Function, Scene, Chaser, EFX, Collection, RGBMatrix, Audio, Video) | Yes |
| `0xB000` | `0xB000` – `0xB005` | Show Manager | Yes |
| `0xC000` | `0xC000` – `0xC001` | Simple Desk | Yes |
| `0xE000` | `0xE000` – `0xE01A` | Virtual Console editing | Yes |
| `0xF000` | `0xF000` – `0xF01D` | **Live actions** (`LIVE_ACTIONS_START_CODE`) | **No** |
| `0xFF00` | `0xFF00` – `0xFF06` | Network protocol commands | **No** |

The full enumeration of assigned codes is in **Appendix C**.

Two boundaries in this layout drive runtime behaviour:

| Boundary | Constant | Effect |
| --- | --- | --- |
| `0xF000` | `LIVE_ACTIONS_START_CODE` | Actions **below** it mark the workspace modified (`Doc::setModified()`) and are recorded in the undo history. Actions at or above it are runtime operations — button presses, slider moves, function start/stop — forwarded to the network but never historised. |
| `0xF004` | `VCButtonSetPressed` | The `Tardis::run()` loop skips history handling entirely for codes at or above this value, forwarding them straight to the network. |

Note that these two thresholds do **not** coincide: codes `0xF000`–`0xF003`
(`FixtureSetDumpValue`, `FixtureResetDumpValues`, `FunctionStart`, `FunctionStop`) are
above `LIVE_ACTIONS_START_CODE` — so they never set the modified flag — yet still fall
through the history path in `run()`.

> **Wire compatibility warning.** The enum uses implicit numbering within each group, so
> **inserting a new code in the middle of a group renumbers every code after it** and
> silently breaks compatibility with older peers — a renumbered action is not rejected,
> it is executed as whatever action now holds that value. New actions must be appended at
> the end of their group's numeric space. The authoritative source is the
> `Tardis::ActionCodes` enum in `qmlui/tardis/tardis.h`.

---

## 5. Network commands

### 5.1 Session commands (`0xFF00` – `0xFF06`)

---

#### NetAnnounce

| | |
| --- | --- |
| **Code** | `0xFF00` |
| **Transmission** | UDP broadcast, port 9997 |
| **Encrypted** | No |

**Payload**

| # | Type | Description |
| --- | --- | --- |
| 1 | `IntType` | Host type (`1` = server, `2` = client) |
| 2 | `StringType` | Host name |

Sent by a QLC+ client when `initializeClient()` is called, to discover the servers
available on the network. The datagram is broadcast on the broadcast address of **every**
non-loopback, non-IPv6 interface.

---

#### NetAnnounceReply

| | |
| --- | --- |
| **Code** | `0xFF01` |
| **Transmission** | UDP unicast to the announcing host, port 9997 |
| **Encrypted** | No |

**Payload**

| # | Type | Description |
| --- | --- | --- |
| 1 | `IntType` | Host type (`1` = server, `2` = client) |
| 2 | `StringType` | Host name |

Sent by every QLC+ host that receives a `NetAnnounce`. A client adds the sender to its
server list **only if the reported host type is `ServerHostType`**, so client-to-client
replies are ignored.

The host type values come from `NetworkManager::HostType`:
`0` = Unknown, `1` = Server, `2` = Client.

---

#### NetAuthentication

| | |
| --- | --- |
| **Code** | `0xFF02` |
| **Transmission** | TCP, port 9998 |
| **Encrypted** | Yes |

**Payload**

| # | Type | Description |
| --- | --- | --- |
| 1 | `ByteArrayType` | The shared key, as a lowercase hexadecimal ASCII string |
| 2 | `StringType` | Host name of the requesting client |

Sent by a client immediately after the TCP connection is established. The client
transmits `QString::number(defaultKey, 16)` — i.e. the ASCII text `5131632b4e33744b` —
and the server accepts the client if the decrypted value matches its own key.

> **Security note.** This is a *shared secret echo*, not a challenge/response: the same
> constant is sent on every connection, so it is replayable by anyone who can capture one
> session. See §6.4.

> **Implementation gap.** `NetworkManager` exposes a `serverPassword` property, persists it
> in the workspace (`InputOutputMap::networkServerPassword`) and lets the user edit it in
> `PopupNetworkServer.qml`, but **the password is never used in the authentication
> exchange**. Authentication currently depends solely on the hard-coded key.

---

#### NetAuthenticationReply

| | |
| --- | --- |
| **Code** | `0xFF03` |
| **Transmission** | TCP, port 9998 |
| **Encrypted** | Yes |

**Payload (success)**

| # | Type | Description |
| --- | --- | --- |
| 1 | `StringType` | `"Success"` |
| 2 | `IntType` | The granted access mask |

**Payload (failure)**

| # | Type | Description |
| --- | --- | --- |
| 1 | `StringType` | `"Failed"` |

The result string is compared literally against `"Success"`; anything else causes the
client to disconnect. Note that the failure reply carries **one** section only — the
access mask is omitted — so a client must not assume section #2 exists.

Key match alone does not produce the reply. On a successful key match the server emits
`clientAccessRequest(hostName)`, which raises a UI prompt; the reply is only sent when the
user resolves it through `setClientAccess()`. A client can therefore sit in
`WaitAuthentication` indefinitely, and must not treat the absence of a prompt reply as a
protocol error.

**Access mask bits** (`App::AccessControl`, `qmlui/app.h`):

| Bit | Value | Permission |
| --- | --- | --- |
| 0 | `0x01` | `AC_FixtureEditing` |
| 1 | `0x02` | `AC_FunctionEditing` |
| 2 | `0x04` | `AC_VCControl` |
| 3 | `0x08` | `AC_VCEditing` |
| 4 | `0x10` | `AC_SimpleDesk` |
| 5 | `0x20` | `AC_ShowManager` |
| 6 | `0x40` | `AC_InputOutput` |

A mask of exactly `AC_VCControl` puts the client into kiosk mode: it is sent straight to
the Virtual Console with no editing UI.

> The access mask is advisory — it drives UI visibility on the client. The server does
> **not** validate incoming action codes against the mask it granted, so a modified client
> can send actions outside its permissions. See §6.4.

---

#### NetPoll

| | |
| --- | --- |
| **Code** | `0xFF04` |
| **Transmission** | TCP, port 9998 |
| **Encrypted** | No |
| **Payload** | None |

Intended as a keep-alive between hosts.

> **Not implemented.** The code is declared in `Tardis::ActionCodes` but is never sent and
> has no handler in `slotProcessTCPPackets()`. Liveness is currently detected only through
> TCP socket disconnection (`slotHostDisconnected()`). Reserved for future use.

---

#### NetPollReply

| | |
| --- | --- |
| **Code** | `0xFF05` |
| **Transmission** | TCP, port 9998 |
| **Encrypted** | No |
| **Payload** | None |

Reply to `NetPoll`. **Not implemented** — see above.

---

#### NetProjectTransfer

| | |
| --- | --- |
| **Code** | `0xFF06` |
| **Transmission** | TCP, port 9998 |
| **Encrypted** | Yes |

**Payload (first chunk, sequence = 0)**

| # | Type | Description |
| --- | --- | --- |
| 1 | `IntType` | Sequence status = `0` (first) |
| 2 | `IntType` | Total project size in bytes |
| 3 | `ByteArrayType` | First chunk of workspace XML |

**Payload (subsequent chunks)**

| # | Type | Description |
| --- | --- | --- |
| 1 | `IntType` | Sequence status — `1` = continue, `2` = last |
| 2 | `ByteArrayType` | Chunk of workspace XML |

**Payload (no project available)**

| # | Type | Description |
| --- | --- | --- |
| 1 | `IntType` | Sequence status = `0` |
| 2 | `IntType` | Total size = `0` |

Used to transfer the workspace XML to a client after successful authentication. The
project is read and sent in chunks of `WORKSPACE_CHUNK_SIZE` = **8192 bytes**.

Chunk classification is by size: a chunk shorter than `WORKSPACE_CHUNK_SIZE` is marked as
last (`2`), a full-size chunk as continue (`1`). The receiver completes the transfer when
it sees sequence `2` **or** when the accumulated byte count reaches the announced total —
whichever comes first. The second condition matters because a project whose size is an
exact multiple of 8192 never produces a short final chunk.

A total size of `0` means the server has no project to send; the client transitions
directly to `Connected`.

Client connection state machine (`NetworkManager::ConnectionStatus`):

```
Disconnected ──connectClient()──▶ WaitAuthentication
                                        │
                          NetAuthenticationReply("Success")
                                        │
                                        ▼
                               DownloadingProject
                                        │
                     last chunk received, or size == 0
                                        │
                                        ▼
                                   Connected
```

---

### 5.2 Action replication (all other codes)

Once a session is established, every Tardis action is replicated with the action code as
the message code. The payload layout is uniform:

| # | Type | Description |
| --- | --- | --- |
| 1 | `IntType` | The object ID the action applies to (`TardisAction::m_objID`) |
| 2…N | *any* | The action value |

`NetworkManager::sendAction()` chooses which value to send based on the action:

| Action group | Value sent |
| --- | --- |
| `FixtureCreate`, `FixtureGroupCreate`, `FunctionCreate`, `ChaserAddStep`, `EFXAddFixture`, `VCWidgetCreate` | `m_newValue` |
| `FixtureDelete`, `FixtureGroupDelete`, `FunctionDelete`, `VCWidgetDelete` | `m_oldValue` |
| everything else | `m_newValue` |

The delete cases send `m_oldValue` because at deletion time the *old* value holds the
serialised object; `m_newValue` is empty.

On receipt, the dispatch in `slotProcessTCPPackets()` is:

| Sections received | Emitted as |
| --- | --- |
| exactly 2 | `actionReady(code, objID, sections[1])` |
| more than 2 | `actionReady(code, objID, sections[1..N])` — a `QVariantList` |
| 1 or 0 | `actionReady(code, objID, QVariant())` |

A packet with an empty section list is dropped with a warning, since the object ID cannot
be recovered.

#### Buffered (XML) actions

Actions that create or destroy objects cannot be expressed as a scalar value: they carry
a **serialised XML fragment** in a `ByteArrayType` section, produced by
`Tardis::actionToByteArray()` and consumed by `Tardis::processBufferedAction()`.

The buffered actions, and the object each one serialises:

| Action pair | Serialised object | Produced by |
| --- | --- | --- |
| `IOAddUniverse` / `IORemoveUniverse` | `Universe` at index `objID` | `Universe::saveXML()` |
| `FixtureCreate` / `FixtureDelete` | `Fixture` with ID `objID` | `Fixture::saveXML()` |
| `FixtureGroupCreate` / `FixtureGroupDelete` | `FixtureGroup` with ID `objID` | `FixtureGroup::saveXML()` |
| `FunctionCreate` / `FunctionDelete` | `Function` with ID `objID` | `Function::saveXML()` |
| `ChaserAddStep` / `ChaserRemoveStep` | `ChaserStep` at index `data` of chaser `objID` | `ChaserStep::saveXML()` |
| `EFXAddFixture` / `EFXRemoveFixture` | `EFXFixture` — reference passed in `data` as `void *` | `EFXFixture::saveXML()` |
| `ShowManagerAddTrack` / `ShowManagerDeleteTrack` | `Track` at index `data` of show `objID` | `Track::saveXML()` |
| `ShowManagerAddFunction` / `ShowManagerDeleteFunction` | `ShowFunction` `data` of show `objID` | `ShowFunction::saveXML()` |
| `VCWidgetCreate` / `VCWidgetDelete` | `VCWidget` with ID `objID` | `VCWidget::saveXML()` |

The XML fragment is the same representation used in the `.qxw` workspace file, so a
receiving peer reconstructs the object with the regular `loadXML()` path. The receiver
distinguishes a buffered action by payload type: if the value is a `QByteArray` it is
parsed as XML, otherwise it falls through to the scalar action handler.

Note that `ChaserStep` and `ShowManager` actions carry an **index or secondary ID in
`data`**, distinct from `objID` which identifies the parent function.

#### Echo suppression

Because the server relays every action to all clients, a naive implementation would send a
client's own change back to it, causing loops and value flapping. Three mechanisms prevent
this:

| Mechanism | Scope | How it works | Implemented in |
| --- | --- | --- | --- |
| **Direct echo guard** | Server, synchronous | While processing a received packet the source socket is held in `m_currentRxSocket`; the relay loop skips that socket. | `sendAction()` |
| **Delayed echo guard** | Server, asynchronous | The source socket is recorded per (action code, object ID) pair. If the same action bounces back within `ECHO_GUARD_WINDOW_MS`, it is suppressed. | `markActionSource()` / `shouldSkipEcho()` |
| **Busy flag** | Client and server | Incoming actions are applied with `Tardis::m_busy = true`, so the resulting local change is not re-enqueued as a new action. | `slotProcessNetworkAction()` |

The delayed guard is **one shot**: the record is consumed on the first match, so a genuine
repeat of the same edit is not lost. A stale record whose socket has been destroyed, or
whose timestamp is older than the window, is dropped rather than matched.

---

## 6. Encryption

### 6.1 What is encrypted

Encryption is applied per packet, and only to the **payload** — the sections. The 7 byte
header is always transmitted in clear, which is what allows the receiver to read the
message code and the payload length before it decrypts anything.

```
        ┌───────────────── 7 bytes ──────────────────┐┌──── encrypted ────┐
        │ 0xE686 │ opCode │ #sections │ sectionsLen  ││  section data...  │
        └────────────────────────────────────────────┘└───────────────────┘
                  clear text                              cipher text
```

`NetworkPacketizer::encryptPacket()` copies the header, encrypts everything after it, then
**rewrites the sections length field (#4)** to the size of the *cipher text*, which
differs from the plaintext size. Field #3 (sections number) still describes the plaintext
and is only meaningful after decryption.

The policy is:

| Traffic | Encrypted |
| --- | --- |
| All UDP traffic (`NetAnnounce`, `NetAnnounceReply`) | **No** — always sent and parsed in clear |
| All TCP traffic | **Yes**, when `m_encryptPackets` is true |

`m_encryptPackets` defaults to `true` in the `NetworkManager` constructor and is not
exposed to the UI, so in practice **every TCP packet is encrypted**. Note this contradicts
the "Encrypted: No" marking historically given to `NetPoll`/`NetPollReply` — since those
travel over TCP, they would be encrypted like everything else if they were implemented.

The receive path mirrors this: `slotProcessUDPPackets()` passes `nullptr` as the
decrypter, `slotProcessTCPPackets()` passes `m_crypt`.

### 6.2 The cipher

Encryption is provided by **SimpleCrypt** (Andre Somers, BSD licensed), a 64 bit
XOR stream cipher with chaining.

**Key.** A single `quint64`. QLC+ uses a hard-coded default:

```cpp
static const quint64 defaultKey = 0x5131632B4E33744B; // this is "Q1c+N3tK"
```

The key is split into 8 bytes (`splitKey()`), little-end first: byte *i* of the key
schedule is `(key >> (8 * i)) & 0xFF`.

**Encryption steps** (`encryptToByteArray()`):

| # | Step | Operation | Flag set |
| --- | --- | --- | --- |
| 1 | **Compress** | `qCompress()` at level 9; result kept only if shorter than the input (mode `CompressionAuto`, the default) | `CryptoFlagCompression` if kept |
| 2 | **Integrity** | 16 bit CRC of the payload, written big endian via `QDataStream` and prepended (mode `ProtectionChecksum`, the default) | `CryptoFlagChecksum` |
| 3 | **Randomise** | One random byte prepended ahead of the checksum — the IV, making identical plaintexts encrypt differently | — |
| 4 | **XOR chain** | For each byte at position *p*: `out[p] = in[p] ^ keyPart[p % 8] ^ out[p-1]`, with `out[-1] = 0` | — |
| 5 | **Frame** | 2 byte prefix prepended: version `0x03`, then the flags byte | — |

Step 4 chains on the *previous cipher byte*, which propagates the random prefix of step 3
through the whole message.

The compression and integrity modes are selectable in the `SimpleCrypt` API, but QLC+
never changes them from their defaults:

| Setting | Available values | Used by QLC+ |
| --- | --- | --- |
| `CompressionMode` | `CompressionAuto`, `CompressionAlways`, `CompressionNever` | `CompressionAuto` |
| `IntegrityProtectionMode` | `ProtectionNone`, `ProtectionChecksum`, `ProtectionHash` | `ProtectionChecksum` |

A decoder must nevertheless honour the flags byte rather than assuming the defaults, since
the mode is recorded per message.

Resulting cipher text layout:

```
┌─────────┬───────┬────────┬──────────────┬───────────────────┐
│ version │ flags │ random │   checksum   │  payload          │
│  0x03   │  u8   │  u8    │   u16 (BE)   │  (maybe deflated) │
└─────────┴───────┴────────┴──────────────┴───────────────────┘
 clear (2 bytes)  └──────────── XOR chained ──────────────────┘
```

**Flags byte:**

| Bit | Value | Meaning |
| --- | --- | --- |
| 0 | `0x01` | `CryptoFlagCompression` — payload is `qCompress`ed |
| 1 | `0x02` | `CryptoFlagChecksum` — 16 bit checksum present |
| 2 | `0x04` | `CryptoFlagHash` — SHA-1 (20 bytes) present |

**Decryption** (`decryptToByteArray()`) reverses the process: verify version `0x03`, read
the flags, undo the XOR chain, drop the random byte, verify and strip the integrity field,
`qUncompress()` if flagged. Any failure returns an **empty** `QByteArray` and sets
`lastError()`:

| `lastError()` | Cause |
| --- | --- |
| `ErrorNoError` | Success |
| `ErrorNoKeySet` | The `SimpleCrypt` instance has no key |
| `ErrorUnknownVersion` | First byte is not `0x03` — wrong version, or not cipher text at all |
| `ErrorIntegrityFailed` | Checksum or SHA-1 mismatch, or the payload is too short to hold the integrity field. Most often means **the wrong key was used**. |

`decodePacket()` treats an empty decryption result as a failed packet: it logs the error
and returns `bytes_read + sections_length` so the caller skips the packet and continues
with the stream.

### 6.3 Implementing an interoperable peer

To speak the protocol from a non-QLC+ implementation:

| # | Step | Transport | Encrypted | Action |
| --- | --- | --- | --- | --- |
| 1 | **Discovery** | UDP 9997 broadcast | No | Send `NetAnnounce` (`0xFF00`) with host type `2` and your host name. Collect `NetAnnounceReply` datagrams; keep only senders reporting host type `1`. |
| 2 | **Connect** | TCP 9998 | — | Open a TCP connection to the chosen server. |
| 3 | **Authenticate** | TCP 9998 | Yes | Send `NetAuthentication` (`0xFF02`): a `ByteArrayType` section holding the ASCII string `5131632b4e33744b`, then a `StringType` section with your host name. |
| 4 | **Wait for approval** | TCP 9998 | Yes | Decrypt `NetAuthenticationReply` (`0xFF03`). Check section #1 equals `"Success"`; read the access mask from section #2 **if present**. May take arbitrarily long — the server prompts its operator first. |
| 5 | **Receive the project** | TCP 9998 | Yes | Reassemble `NetProjectTransfer` (`0xFF06`) chunks into the workspace XML. Stop on sequence `2` **or** when the announced total is reached. |
| 6 | **Run** | TCP 9998 | Yes | Decrypt each inbound packet, dispatch on the message code, encrypt each outbound one. |

Pitfalls worth designing against up front:

| Pitfall | Consequence if ignored |
| --- | --- |
| TCP coalesces and splits packets | Always drive the read loop off the sections length field and handle the `-1` "need more data" return; never assume one read equals one packet. |
| Field #4 means cipher text length | On an encrypted packet the length describes the *encrypted* payload; field #3 (sections number) describes the plaintext and is only valid after decryption. |
| Failure replies are shorter | `NetAuthenticationReply` carries one section on failure, two on success. |
| Floats are native endian | See §3 — assume little endian IEEE-754. |
| Action codes are positionally numbered | See §4 — never renumber; a version mismatch silently misroutes actions. |

Minimal reference — building an encrypted packet:

```
packet  = 0xE6 0x86                      # protocol ID
        | opCode >> 8 | opCode & 0xFF    # message code
        | 0x00                           # sections number  (patched)
        | 0x00 0x00                      # sections length  (patched)

for each value:
    append section type, optional u16 length, data
    sections_number += 1
    sections_length  = len(packet) - 7
    patch bytes 4, 5, 6

cipher  = simplecrypt_encrypt(packet[7:], key)
out     = packet[0:7] + cipher
patch out[5], out[6] = len(cipher) >> 8, len(cipher) & 0xFF
```

### 6.4 Security considerations

The encryption in this protocol is **obfuscation, not security**. SimpleCrypt's own
documentation states it plainly: *"The encryption provided by this class is NOT strong
encryption. It may help to shield things from curious eyes, but it will NOT stand up to
someone determined to break the encryption."*

Specific weaknesses, so that deployments can be planned realistically:

| Weakness | Detail | Consequence |
| --- | --- | --- |
| **Key is a compile-time constant** | `defaultKey` is identical in every QLC+ build; not secret, not negotiated, not user-changeable | Anyone with a copy of QLC+ can decrypt any session |
| **Authentication is a replayable echo** | The same constant is sent on every connection; no nonce, no challenge/response | Capturing one handshake is enough to authenticate later |
| **Server password is never checked** | Stored, persisted and displayed, but no code path compares it during authentication (§5.1) | The password provides no protection whatsoever |
| **64 bit repeating XOR keystream** | Key schedule repeats every 8 bytes, with cipher-block chaining over it | No confidentiality against a known key; brute force trivial even if the key were secret |
| **Integrity is a CRC, not a MAC** | 16 bit checksum with no keyed authentication | Detects corruption, not tampering — an attacker who knows the key can forge valid packets |
| **Checksum covers a truncated prefix** | `Utils::getChecksum(ba.constData())` converts `const char*` → `QByteArray`, stopping at the first NUL; compressed payloads are full of NULs | The corruption check covers only a leading prefix. Symmetric between peers, so interop is unaffected |
| **Access mask not enforced server-side** | The mask drives client UI visibility only; incoming action codes are not validated against it | A modified client can issue actions beyond what it was granted |
| **Header is transmitted in clear** | Protocol ID, message code and lengths are never encrypted | Message codes and traffic patterns are visible to a passive observer without the key |

**Deployment recommendation.** Treat a QLC+ 5 network session as *unauthenticated plain
text* for threat-modelling purposes. Run it only on a trusted, isolated show LAN — a
dedicated lighting VLAN or an access-controlled Wi-Fi. Do not expose ports 9997/9998 to the
internet, and if remote access is required, tunnel the session over a VPN or SSH rather
than relying on the built-in encryption.

**Path to real security**, should the protocol be hardened later:

| Change | Addresses | Notes |
| --- | --- | --- |
| Move the transport to TLS (`QSslSocket`) | Confidentiality, tampering, header exposure | Reduces SimpleCrypt to a legacy compatibility path |
| Derive the session key from the server password with a PBKDF | Constant shared key | The password field already exists and is persisted — only the verification is missing |
| Make the handshake a challenge/response with a server-supplied nonce | Replay | Removes the fixed authentication token |
| Validate incoming action codes against the granted access mask server-side | Unenforced permissions | The mask is already transmitted; only the check is missing |
| Replace the CRC with a keyed MAC | Forgery | Also fixes the NUL-truncation defect as a side effect |

Two existing fields leave room to negotiate such an upgrade without breaking older peers:
the SimpleCrypt version byte (`0x03`) and the protocol ID (`0xE686`), which doubles as the
protocol version.

---

## 7. Implementation status summary

| Feature | Status |
| --- | --- |
| UDP discovery (`NetAnnounce` / `NetAnnounceReply`) | Implemented |
| TCP authentication (`NetAuthentication` / `Reply`) | Implemented, hard-coded key |
| Server password check | **Declared and stored, never verified** |
| Access mask negotiation | Implemented (advisory, not enforced server-side) |
| Project transfer (`NetProjectTransfer`) | Implemented, 8 KB chunks |
| Keep-alive (`NetPoll` / `NetPollReply`) | **Declared, not implemented** |
| Action replication | Implemented for all Tardis action codes |
| Buffered (XML) actions | Implemented for 18 create/delete actions |
| Echo suppression | Implemented (direct + 15 s delayed guard) |
| Payload encryption | Implemented (SimpleCrypt, TCP only) |
| Float endianness normalisation | **Not implemented** — native byte order |
| Malformed packet recovery | Implemented (bounds-checked decode, packet skip) |

---

## Appendix A — Constants

| Constant | Value | Defined in |
| --- | --- | --- |
| Protocol ID | `0xE686` | `networkpacketizer.cpp` |
| `HEADER_LENGTH` | `7` | `networkpacketizer.h` |
| `DEFAULT_UDP_PORT` | `9997` | `networkmanager.cpp` |
| `DEFAULT_TCP_PORT` | `9998` | `networkmanager.cpp` |
| `WORKSPACE_CHUNK_SIZE` | `8 * 1024` | `networkmanager.cpp` |
| `ECHO_GUARD_WINDOW_MS` | `15000` | `networkmanager.cpp` |
| `defaultKey` | `0x5131632B4E33744B` (`"Q1c+N3tK"`) | `networkmanager.cpp` |
| `LIVE_ACTIONS_START_CODE` | `0xF000` | `tardis.h` |
| SimpleCrypt version byte | `0x03` | `simplecrypt.cpp` |

## Appendix B — Source files

| File | Responsibility |
| --- | --- |
| `qmlui/tardis/tardis.h` | `ActionCodes` enum — the message code vocabulary |
| `qmlui/tardis/tardis.cpp` | Action queue, undo/redo history, XML serialisation of buffered actions |
| `qmlui/tardis/networkpacketizer.h/.cpp` | Packet build/parse, section types, encrypt/decrypt framing |
| `qmlui/tardis/networkmanager.h/.cpp` | Sockets, discovery, authentication, project transfer, echo suppression |
| `qmlui/tardis/simplecrypt.h/.cpp` | The SimpleCrypt cipher |
| `qmlui/app.h` | `AccessControl` mask bits |
| `engine/src/inputoutputmap.h/.cpp` | Persistence of the network server settings in the workspace |

## Appendix C — Message code reference

Complete enumeration of the assigned `Tardis::ActionCodes` values, generated from
`qmlui/tardis/tardis.h`. Codes marked **XML** carry a serialised object as a
`ByteArrayType` section rather than a scalar value (see §5.2).

| Code | Action | Group | XML |
| --- | --- | --- | --- |
| `0x0000` | `EnvironmentSetSize` | Preview settings |  |
| `0x0001` | `EnvironmentBackgroundImage` | Preview settings |  |
| `0x0002` | `FixtureSetPosition` | Preview settings |  |
| `0x0003` | `FixtureSetRotation` | Preview settings |  |
| `0x0004` | `GenericItemSetPosition` | Preview settings |  |
| `0x0005` | `GenericItemSetRotation` | Preview settings |  |
| `0x0006` | `GenericItemSetScale` | Preview settings |  |
| `0x0090` | `IOAddUniverse` | Preview settings | ● |
| `0x0091` | `IORemoveUniverse` | Preview settings | ● |
| `0x0100` | `FixtureCreate` | Fixture editing | ● |
| `0x0101` | `FixtureDelete` | Fixture editing | ● |
| `0x0102` | `FixtureMove` | Fixture editing |  |
| `0x0103` | `FixtureSetName` | Fixture editing |  |
| `0x0104` | `FixtureSetChannelModifier` | Fixture editing |  |
| `0x0105` | `FixtureGroupCreate` | Fixture group editing | ● |
| `0x0106` | `FixtureGroupDelete` | Fixture group editing | ● |
| `0x0200` | `FunctionCreate` | Function editing | ● |
| `0x0201` | `FunctionDelete` | Function editing | ● |
| `0x0202` | `FunctionSetName` | Function editing |  |
| `0x0203` | `FunctionSetPath` | Function editing |  |
| `0x0204` | `FunctionSetRunOrder` | Function editing |  |
| `0x0205` | `FunctionSetDirection` | Function editing |  |
| `0x0206` | `FunctionSetTempoType` | Function editing |  |
| `0x0207` | `FunctionSetFadeIn` | Function editing |  |
| `0x0208` | `FunctionSetFadeOut` | Function editing |  |
| `0x0209` | `FunctionSetDuration` | Function editing |  |
| `0x020A` | `SceneSetChannelValue` | Function editing |  |
| `0x020B` | `SceneUnsetChannelValue` | Function editing |  |
| `0x020C` | `SceneAddFixture` | Function editing |  |
| `0x020D` | `SceneRemoveFixture` | Function editing |  |
| `0x020E` | `SceneAddFixtureGroup` | Function editing |  |
| `0x020F` | `SceneRemoveFixtureGroup` | Function editing |  |
| `0x0210` | `SceneAddPalette` | Function editing |  |
| `0x0211` | `SceneRemovePalette` | Function editing |  |
| `0x0212` | `ChaserAddStep` | Function editing | ● |
| `0x0213` | `ChaserRemoveStep` | Function editing | ● |
| `0x0214` | `ChaserMoveStep` | Function editing |  |
| `0x0215` | `ChaserSetStepFadeIn` | Function editing |  |
| `0x0216` | `ChaserSetStepHold` | Function editing |  |
| `0x0217` | `ChaserSetStepFadeOut` | Function editing |  |
| `0x0218` | `ChaserSetStepDuration` | Function editing |  |
| `0x0219` | `EFXAddFixture` | Function editing | ● |
| `0x021A` | `EFXRemoveFixture` | Function editing | ● |
| `0x021B` | `EFXFixturePropagation` | Function editing |  |
| `0x021C` | `EFXSetAlgorithmIndex` | Function editing |  |
| `0x021D` | `EFXSetRelative` | Function editing |  |
| `0x021E` | `EFXSetWidth` | Function editing |  |
| `0x021F` | `EFXSetHeight` | Function editing |  |
| `0x0220` | `EFXSetXOffset` | Function editing |  |
| `0x0221` | `EFXSetYOffset` | Function editing |  |
| `0x0222` | `EFXSetRotation` | Function editing |  |
| `0x0223` | `EFXSetStartOffset` | Function editing |  |
| `0x0224` | `EFXSetXFrequency` | Function editing |  |
| `0x0225` | `EFXSetYFrequency` | Function editing |  |
| `0x0226` | `EFXSetXPhase` | Function editing |  |
| `0x0227` | `EFXSetYPhase` | Function editing |  |
| `0x0228` | `CollectionAddFunction` | Function editing |  |
| `0x0229` | `CollectionRemoveFunction` | Function editing |  |
| `0x022A` | `RGBMatrixSetFixtureGroup` | Function editing |  |
| `0x022B` | `RGBMatrixSetAlgorithmIndex` | Function editing |  |
| `0x022C` | `RGBMatrixSetColor1` | Function editing |  |
| `0x022D` | `RGBMatrixSetColor2` | Function editing |  |
| `0x022E` | `RGBMatrixSetColor3` | Function editing |  |
| `0x022F` | `RGBMatrixSetColor4` | Function editing |  |
| `0x0230` | `RGBMatrixSetColor5` | Function editing |  |
| `0x0231` | `RGBMatrixSetScriptIntValue` | Function editing |  |
| `0x0232` | `RGBMatrixSetScriptDoubleValue` | Function editing |  |
| `0x0233` | `RGBMatrixSetScriptStringValue` | Function editing |  |
| `0x0234` | `RGBMatrixSetText` | Function editing |  |
| `0x0235` | `RGBMatrixSetTextFont` | Function editing |  |
| `0x0236` | `RGBMatrixSetImage` | Function editing |  |
| `0x0237` | `RGBMatrixSetOffset` | Function editing |  |
| `0x0238` | `RGBMatrixSetAnimationStyle` | Function editing |  |
| `0x0239` | `AudioSetSource` | Function editing |  |
| `0x023A` | `AudioSetVolume` | Function editing |  |
| `0x023B` | `VideoSetSource` | Function editing |  |
| `0x023C` | `VideoSetScreenIndex` | Function editing |  |
| `0x023D` | `VideoSetFullscreen` | Function editing |  |
| `0x023E` | `VideoSetGeometry` | Function editing |  |
| `0x023F` | `VideoSetRotation` | Function editing |  |
| `0x0240` | `VideoSetLayer` | Function editing |  |
| `0xB000` | `ShowManagerAddTrack` | Show Manager | ● |
| `0xB001` | `ShowManagerDeleteTrack` | Show Manager | ● |
| `0xB002` | `ShowManagerAddFunction` | Show Manager | ● |
| `0xB003` | `ShowManagerDeleteFunction` | Show Manager | ● |
| `0xB004` | `ShowManagerItemSetStartTime` | Show Manager |  |
| `0xB005` | `ShowManagerItemSetDuration` | Show Manager |  |
| `0xC000` | `SimpleDeskSetChannel` | Simple Desk |  |
| `0xC001` | `SimpleDeskResetChannel` | Simple Desk |  |
| `0xE000` | `VCWidgetCreate` | Virtual console editing | ● |
| `0xE001` | `VCWidgetDelete` | Virtual console editing | ● |
| `0xE002` | `VCWidgetGeometry` | Virtual console editing |  |
| `0xE003` | `VCWidgetReparent` | Virtual console editing |  |
| `0xE004` | `VCWidgetAllowResize` | Virtual console editing |  |
| `0xE005` | `VCWidgetDisabled` | Virtual console editing |  |
| `0xE006` | `VCWidgetVisible` | Virtual console editing |  |
| `0xE007` | `VCWidgetCaption` | Virtual console editing |  |
| `0xE008` | `VCWidgetBackgroundColor` | Virtual console editing |  |
| `0xE009` | `VCWidgetBackgroundImage` | Virtual console editing |  |
| `0xE00A` | `VCWidgetForegroundColor` | Virtual console editing |  |
| `0xE00B` | `VCWidgetFont` | Virtual console editing |  |
| `0xE00C` | `VCWidgetPage` | Virtual console editing |  |
| `0xE00D` | `VCWidgetZIndex` | Virtual console editing |  |
| `0xE00E` | `VCButtonSetActionType` | Virtual console editing |  |
| `0xE00F` | `VCButtonSetFunctionID` | Virtual console editing |  |
| `0xE010` | `VCButtonEnableStartupIntensity` | Virtual console editing |  |
| `0xE011` | `VCButtonSetStartupIntensity` | Virtual console editing |  |
| `0xE012` | `VCSliderSetMode` | Virtual console editing |  |
| `0xE013` | `VCSliderSetWidgetStyle` | Virtual console editing |  |
| `0xE014` | `VCSliderSetDisplayStyle` | Virtual console editing |  |
| `0xE015` | `VCSliderSetInverted` | Virtual console editing |  |
| `0xE016` | `VCSliderSetFunctionID` | Virtual console editing |  |
| `0xE017` | `VCSliderSetControlledAttribute` | Virtual console editing |  |
| `0xE018` | `VCSliderSetLowLimit` | Virtual console editing |  |
| `0xE019` | `VCSliderSetHighLimit` | Virtual console editing |  |
| `0xE01A` | `VCCueListSetChaserID` | Virtual console editing |  |
| `0xF000` | `FixtureSetDumpValue` | Live |  |
| `0xF001` | `FixtureResetDumpValues` | Live |  |
| `0xF002` | `FunctionStart` | Live |  |
| `0xF003` | `FunctionStop` | Live |  |
| `0xF004` | `VCButtonSetPressed` | Live |  |
| `0xF005` | `VCSliderSetValue` | Live |  |
| `0xF006` | `VCSliderButtonPress` | Live |  |
| `0xF007` | `VCCueListPlayClicked` | Live |  |
| `0xF008` | `VCCueListStopClicked` | Live |  |
| `0xF009` | `VCCueListNextClicked` | Live |  |
| `0xF00A` | `VCCueListPreviousClicked` | Live |  |
| `0xF00B` | `VCCueListSetIndex` | Live |  |
| `0xF00C` | `VCSpeedDialSetTime` | Live |  |
| `0xF00D` | `VCSpeedDialSetFactor` | Live |  |
| `0xF00E` | `VCSpeedDialApply` | Live |  |
| `0xF00F` | `VCXYPadSetPosition` | Live |  |
| `0xF010` | `VCXYPadSetGeometry` | Live |  |
| `0xF011` | `VCXYPadActivatePreset` | Live |  |
| `0xF012` | `VCAudioTriggersSetCaptureEnabled` | Live |  |
| `0xF013` | `VCAudioTriggersSetLevel` | Live |  |
| `0xF014` | `VCClockSetEnabled` | Live |  |
| `0xF015` | `VCClockReset` | Live |  |
| `0xF016` | `VCAnimationSetFaderLevel` | Live |  |
| `0xF017` | `VCAnimationSetAlgorithmIndex` | Live |  |
| `0xF018` | `VCAnimationSetColor1` | Live |  |
| `0xF019` | `VCAnimationSetColor2` | Live |  |
| `0xF01A` | `VCAnimationSetColor3` | Live |  |
| `0xF01B` | `VCAnimationSetColor4` | Live |  |
| `0xF01C` | `VCAnimationSetColor5` | Live |  |
| `0xF01D` | `VCAnimationActivatePreset` | Live |  |
| `0xFF00` | `NetAnnounce` | Network protocol |  |
| `0xFF01` | `NetAnnounceReply` | Network protocol |  |
| `0xFF02` | `NetAuthentication` | Network protocol |  |
| `0xFF03` | `NetAuthenticationReply` | Network protocol |  |
| `0xFF04` | `NetPoll` | Network protocol |  |
| `0xFF05` | `NetPollReply` | Network protocol |  |
| `0xFF06` | `NetProjectTransfer` | Network protocol |  |

Codes `0xF000` and above are live actions: forwarded to the network, but never recorded in
the undo history (see §4).
