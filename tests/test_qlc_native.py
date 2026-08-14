import hashlib
import unittest
from unittest.mock import patch

from oculizer.light.qlc_native import (
    DEFAULT_KEY,
    NET_AUTHENTICATION_REPLY,
    NET_PROJECT_TRANSFER,
    NativeState,
    NativeWidget,
    QLCNativeClient,
    QLCNativeError,
    VC_BUTTON_SET_PRESSED,
    _decrypt,
    _encrypt,
    _key_parts,
    _packet,
    _parse_sections,
    _recv_packet,
    _section_bool,
    _section_bytearray,
    _section_int,
    _section_string,
    _session_key,
    parse_project_inventory,
)


class QLCNativeTests(unittest.TestCase):
    class FragmentedSocket:
        def __init__(self, data, fragment_size=1):
            self.data = bytearray(data)
            self.fragment_size = fragment_size

        def recv(self, size):
            size = min(size, self.fragment_size, len(self.data))
            result = bytes(self.data[:size])
            del self.data[:size]
            return result

        def settimeout(self, _timeout):
            pass

        def sendall(self, data):
            self.sent = getattr(self, "sent", []) + [data]

        def shutdown(self, _how):
            pass

        def close(self):
            pass

    def test_fixed_packet_vector_matches_qlc_simplecrypt_contract(self):
        expected = bytes.fromhex(
            "e686f20002000c030211844609224170662d58"
        )
        with patch("oculizer.light.qlc_native.os.urandom", return_value=b"\x5a"):
            packet = _packet(
                VC_BUTTON_SET_PRESSED, DEFAULT_KEY,
                _section_int(71), _section_bool(True),
            )
        self.assertEqual(packet, expected)
        opcode, sections = _recv_packet(self.FragmentedSocket(expected), DEFAULT_KEY)
        self.assertEqual(opcode, VC_BUTTON_SET_PRESSED)
        self.assertEqual(sections, [71, True])

    def test_coalesced_packets_remain_separate(self):
        with patch("oculizer.light.qlc_native.os.urandom", return_value=b"\x5a"):
            first = _packet(NET_AUTHENTICATION_REPLY, DEFAULT_KEY, _section_string("Success"))
            second = _packet(NET_PROJECT_TRANSFER, DEFAULT_KEY, _section_int(0), _section_int(0))
        stream = self.FragmentedSocket(first + second, fragment_size=4096)
        self.assertEqual(_recv_packet(stream, DEFAULT_KEY), (NET_AUTHENTICATION_REPLY, ["Success"]))
        self.assertEqual(_recv_packet(stream, DEFAULT_KEY), (NET_PROJECT_TRANSFER, [0, 0]))

    def test_tcp_disconnect_during_packet_is_explicit(self):
        with self.assertRaisesRegex(ConnectionError, "disconnected"):
            _recv_packet(self.FragmentedSocket(b"\xe6\x86"), DEFAULT_KEY)

    def test_truncated_and_malformed_sections_are_rejected(self):
        invalid_payloads = (
            (b"", 1),
            (b"\x01\x00", 1),
            (b"\x00\x02", 1),
            (b"\x03\x00\x04ab", 1),
            (_section_int(1) + b"extra", 1),
        )
        for payload, count in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                _parse_sections(payload, count)

    def test_simplecrypt_rejects_truncation_flags_crc_and_size(self):
        for payload in (b"", b"\x03", b"\x03\x80\x00", b"\x03\x02\x00"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                _decrypt(payload, DEFAULT_KEY)

        with patch("oculizer.light.qlc_native.os.urandom", return_value=b"\x5a"):
            encrypted = bytearray(_packet(
                NET_AUTHENTICATION_REPLY, DEFAULT_KEY, _section_string("Success"),
            )[7:])
        # SimpleCrypt's QLC-compatible CRC stops at the first NUL byte, so
        # corrupt the first section tag rather than data after its length NUL.
        encrypted[5] ^= 0x01
        with self.assertRaisesRegex(ValueError, "CRC"):
            _decrypt(bytes(encrypted), DEFAULT_KEY)

        with patch("oculizer.light.qlc_native.os.urandom", return_value=b"\x5a"):
            compressed = _encrypt(b"x" * 4096, DEFAULT_KEY)
        with self.assertRaisesRegex(ValueError, "safe limit|oversized"):
            _decrypt(compressed, DEFAULT_KEY, maximum_size=128)

    def test_simplecrypt_accepts_declared_sha1_integrity_mode(self):
        payload = _section_string("future-compatible")
        clear = bytearray(b"\x5a" + hashlib.sha1(payload).digest() + payload)
        previous = 0
        parts = _key_parts(DEFAULT_KEY)
        for index in range(len(clear)):
            encrypted = clear[index] ^ parts[index % 8] ^ previous
            clear[index] = encrypted
            previous = encrypted
        ciphertext = b"\x03\x04" + bytes(clear)
        self.assertEqual(_decrypt(ciphertext, DEFAULT_KEY), payload)

    def test_reconnect_state_logs_are_rate_limited_without_hiding_state(self):
        client = QLCNativeClient("127.0.0.1")
        with (
            patch("oculizer.light.qlc_native.time.monotonic", side_effect=(0.0, 1.0, 2.0, 31.0)),
            patch("oculizer.light.qlc_native.logger.info") as log,
        ):
            self.assertTrue(client._set_state(NativeState.CONNECTING))
            self.assertTrue(client._set_state(NativeState.DISCONNECTED))
            self.assertFalse(client._set_state(NativeState.CONNECTING))
            self.assertTrue(client._set_state(NativeState.DISCONNECTED))

        self.assertEqual(client.state, NativeState.DISCONNECTED)
        self.assertEqual(log.call_count, 3)

    def test_ready_state_resets_reconnect_incident_log_suppression(self):
        client = QLCNativeClient("127.0.0.1")
        client._last_error = "connection refused"
        with (
            patch("oculizer.light.qlc_native.time.monotonic", side_effect=(0.0, 1.0, 2.0)),
            patch("oculizer.light.qlc_native.logger.info") as log,
        ):
            self.assertTrue(client._set_state(NativeState.CONNECTING))
            self.assertTrue(client._set_state(NativeState.READY))
            self.assertTrue(client._set_state(NativeState.CONNECTING))

        self.assertIsNone(client._last_error)
        self.assertEqual(log.call_count, 3)

    def test_empty_encryption_key_uses_qlc_default_key(self):
        self.assertEqual(_session_key(""), DEFAULT_KEY)

    def test_custom_encryption_key_matches_qlc_sha256_folding(self):
        expected = int.from_bytes(hashlib.sha256(b"ronron").digest()[:8], "big")
        self.assertEqual(_session_key("ronron"), expected)

    def test_project_inventory_discovers_buttons_sliders_and_ranges(self):
        buttons, sliders = parse_project_inventory(b'''<!DOCTYPE Workspace>
        <Workspace><VirtualConsole>
          <Frame ID="1" Caption="Scenes">
            <SoloFrame ID="2" Caption="Exclusive">
              <Button ID="71" Caption="Party">
                <Function ID="42"/><Action>Toggle</Action>
              </Button>
            </SoloFrame>
            <Slider ID="72" Caption="Master" WidgetStyle="Fader">
              <SliderMode>Playback</SliderMode>
              <Level LowLimit="10" HighLimit="210" Value="50"/>
              <Adjust Function="43" Attribute="0"/>
            </Slider>
          </Frame>
        </VirtualConsole></Workspace>''')
        button = buttons["party"]
        slider = sliders["master"]
        self.assertEqual(button.widget_id, 71)
        self.assertEqual(button.action_type, "toggle")
        self.assertEqual(button.function_id, 42)
        self.assertEqual(button.parent_frame_kind, "soloframe")
        self.assertEqual(button.parent_frame_id, 2)
        self.assertEqual(button.frame_path, ("Scenes", "Exclusive"))
        self.assertEqual(slider.widget_id, 72)
        self.assertEqual((slider.low, slider.high), (10.0, 210.0))
        self.assertEqual(slider.slider_mode, "playback")
        self.assertEqual(slider.widget_style, "Fader")
        self.assertEqual(slider.function_id, 43)
        self.assertEqual(slider.parent_frame_kind, "frame")
        self.assertEqual(slider.frame_path, ("Scenes",))

    def test_optional_metadata_does_not_reject_usable_widgets(self):
        buttons, _ = parse_project_inventory(b'''<Workspace><VirtualConsole>
          <Frame ID="future" Caption="Scenes"><Button ID="71" Caption="Party">
            <Function ID="not-yet-readable"/><Action>FutureAction</Action>
          </Button></Frame>
        </VirtualConsole></Workspace>''')
        self.assertEqual(buttons["party"].widget_id, 71)
        self.assertIsNone(buttons["party"].parent_frame_id)
        self.assertIsNone(buttons["party"].function_id)
        self.assertEqual(buttons["party"].action_type, "futureaction")

    def test_ready_inventory_rejects_wrong_widget_kind_without_queueing(self):
        client = QLCNativeClient("127.0.0.1")
        client.state = NativeState.READY
        client.buttons = {"party": NativeWidget(1, "Party", "button")}
        client.sliders = {"master": NativeWidget(2, "Master", "slider")}
        self.assertFalse(client.activate_button("Master"))
        self.assertFalse(client.set_slider_level("Party", 0.5))
        self.assertIsNone(client._pending_scene)
        self.assertEqual(client._pending_parameters, {})

    def test_button_release_is_sent_only_for_flash_action(self):
        connection = self.FragmentedSocket(b"", fragment_size=4096)
        client = QLCNativeClient("127.0.0.1", button_release_seconds=0)
        client.socket = connection
        client.state = NativeState.READY
        actions = ("toggle", "blackout", "stopall", "futureaction")
        client.buttons = {
            action: NativeWidget(index, action, "button", action_type=action)
            for index, action in enumerate(actions, 1)
        }
        client.buttons["flash"] = NativeWidget(9, "Flash", "button", action_type="flash")

        for index, action in enumerate(actions, 1):
            self.assertTrue(client.activate_button(action))
            client._flush_pending()
            _, sections = _recv_packet(
                self.FragmentedSocket(connection.sent[-1]), DEFAULT_KEY,
            )
            self.assertEqual(sections, [index, True])
        self.assertEqual(len(connection.sent), len(actions))

        self.assertTrue(client.activate_button("Flash"))
        client._flush_pending()
        self.assertEqual(len(connection.sent), len(actions) + 2)
        _, press_sections = _recv_packet(
            self.FragmentedSocket(connection.sent[-2]), DEFAULT_KEY,
        )
        _, release_sections = _recv_packet(
            self.FragmentedSocket(connection.sent[-1]), DEFAULT_KEY,
        )
        self.assertEqual(press_sections, [9, True])
        self.assertEqual(release_sections, [9, False])

    def test_project_inventory_ignores_non_virtual_console_elements(self):
        buttons, sliders = parse_project_inventory(b'''<Workspace>
          <Engine><Button ID="1" Caption="Not a widget" /></Engine>
          <VirtualConsole><Button ID="2" Caption="Party" /></VirtualConsole>
        </Workspace>''')
        self.assertEqual(tuple(buttons), ("party",))
        self.assertEqual(sliders, {})

    def test_project_inventory_rejects_unsafe_or_invalid_xml(self):
        samples = (
            b'<!DOCTYPE Workspace [<!ENTITY x "value">]><Workspace>&x;</Workspace>',
            b'<!DOCTYPE Workspace SYSTEM "workspace.dtd"><Workspace />',
            b'<Workspace><VirtualConsole><Slider ID="1" Caption="Bad"><Value Low="5" High="5" /></Slider></VirtualConsole></Workspace>',
            b'<Workspace><VirtualConsole><Button ID="x" Caption="Bad" /></VirtualConsole></Workspace>',
        )
        for xml in samples:
            with self.subTest(xml=xml), self.assertRaises(QLCNativeError):
                parse_project_inventory(xml)
        with self.assertRaisesRegex(QLCNativeError, "exceeds"):
            parse_project_inventory(b"<Workspace />", maximum_size=4)

    def test_project_transfer_replaces_inventory_only_after_complete_parse(self):
        xml = (
            b'<Workspace><VirtualConsole><Button ID="9" Caption="New" />'
            b'</VirtualConsole></Workspace>'
        )
        with patch("oculizer.light.qlc_native.os.urandom", return_value=b"\x5a"):
            response = b"".join((
                _packet(
                    NET_AUTHENTICATION_REPLY, DEFAULT_KEY,
                    _section_string("Success"), _section_int(127),
                ),
                _packet(
                    NET_PROJECT_TRANSFER, DEFAULT_KEY,
                    _section_int(0), _section_int(len(xml)), _section_bytearray(xml),
                ),
            ))
        connection = self.FragmentedSocket(response, fragment_size=3)
        client = QLCNativeClient("127.0.0.1")
        client.buttons = {"old": NativeWidget(1, "Old", "button")}
        with patch("oculizer.light.qlc_native.socket.create_connection", return_value=connection):
            client._connect()
        self.assertEqual(client.state, NativeState.READY)
        self.assertEqual(tuple(client.buttons), ("new",))

    def test_session_ignores_unknown_opcode_and_trailing_extensions(self):
        xml = (
            b'<Workspace><VirtualConsole><Button ID="9" Caption="Compatible" />'
            b'</VirtualConsole></Workspace>'
        )
        future_section = b"\x7f\xde\xad\xbe\xef"
        with patch("oculizer.light.qlc_native.os.urandom", return_value=b"\x5a"):
            response = b"".join((
                _packet(0xFE10, DEFAULT_KEY, future_section),
                _packet(
                    NET_AUTHENTICATION_REPLY, DEFAULT_KEY,
                    _section_string("Success"), _section_int(127), future_section,
                ),
                _packet(
                    NET_PROJECT_TRANSFER, DEFAULT_KEY,
                    _section_int(0), _section_int(len(xml)),
                    _section_bytearray(xml), future_section,
                ),
            ))
        client = QLCNativeClient("127.0.0.1")
        with patch(
            "oculizer.light.qlc_native.socket.create_connection",
            return_value=self.FragmentedSocket(response, fragment_size=7),
        ):
            client._connect()
        self.assertEqual(client.state, NativeState.READY)
        self.assertEqual(tuple(client.buttons), ("compatible",))

    def test_authentication_refusal_is_explicit(self):
        with patch("oculizer.light.qlc_native.os.urandom", return_value=b"\x5a"):
            response = _packet(
                NET_AUTHENTICATION_REPLY, DEFAULT_KEY, _section_string("Denied"),
            )
        client = QLCNativeClient("127.0.0.1")
        with (
            patch(
                "oculizer.light.qlc_native.socket.create_connection",
                return_value=self.FragmentedSocket(response),
            ),
            self.assertRaisesRegex(QLCNativeError, "authorization was refused"),
        ):
            client._connect()

    def test_incomplete_project_does_not_replace_existing_inventory(self):
        xml = b"<Workspace />"
        with patch("oculizer.light.qlc_native.os.urandom", return_value=b"\x5a"):
            response = b"".join((
                _packet(
                    NET_AUTHENTICATION_REPLY, DEFAULT_KEY,
                    _section_string("Success"), _section_int(127),
                ),
                _packet(
                    NET_PROJECT_TRANSFER, DEFAULT_KEY,
                    _section_int(0), _section_int(len(xml) + 1), _section_bytearray(xml),
                ),
                _packet(
                    NET_PROJECT_TRANSFER, DEFAULT_KEY,
                    _section_int(2), _section_bytearray(b""),
                ),
            ))
        client = QLCNativeClient("127.0.0.1")
        old = NativeWidget(1, "Old", "button")
        client.buttons = {"old": old}
        with (
            patch(
                "oculizer.light.qlc_native.socket.create_connection",
                return_value=self.FragmentedSocket(response, fragment_size=5),
            ),
            self.assertRaisesRegex(QLCNativeError, "ended before declared size"),
        ):
            client._connect()
        self.assertEqual(client.buttons, {"old": old})

    def test_exact_8192_byte_project_completes_without_last_sequence(self):
        prefix = b'<Workspace><VirtualConsole><Button ID="9" Caption="Exact" />'
        suffix = b"</VirtualConsole></Workspace>"
        xml = prefix + (b" " * (8192 - len(prefix) - len(suffix))) + suffix
        self.assertEqual(len(xml), 8192)
        with patch("oculizer.light.qlc_native.os.urandom", return_value=b"\x5a"):
            response = b"".join((
                _packet(
                    NET_AUTHENTICATION_REPLY, DEFAULT_KEY,
                    _section_string("Success"), _section_int(127),
                ),
                _packet(
                    NET_PROJECT_TRANSFER, DEFAULT_KEY,
                    _section_int(0), _section_int(len(xml)), _section_bytearray(xml),
                ),
            ))
        client = QLCNativeClient("127.0.0.1")
        with patch(
            "oculizer.light.qlc_native.socket.create_connection",
            return_value=self.FragmentedSocket(response, fragment_size=97),
        ):
            client._connect()
        self.assertEqual(tuple(client.buttons), ("exact",))


if __name__ == "__main__":
    unittest.main()
