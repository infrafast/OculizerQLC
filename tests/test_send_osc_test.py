import socket
import struct
import threading
import unittest

from scripts.send_osc_test import build_float_message, send_float


def decode_single_float_message(packet: bytes) -> tuple[str, float]:
    address_end = packet.index(b"\x00")
    address = packet[:address_end].decode("utf-8")
    type_start = (address_end + 4) & ~3
    type_end = packet.index(b"\x00", type_start)
    type_tag = packet[type_start:type_end]
    value_start = (type_end + 4) & ~3

    if type_tag != b",f":
        raise ValueError(f"Unexpected OSC type tag: {type_tag!r}")
    value = struct.unpack(">f", packet[value_start : value_start + 4])[0]
    return address, value


class OscTestSenderTests(unittest.TestCase):
    def test_build_float_message_uses_osc_padding_and_big_endian_float(self):
        packet = build_float_message("/test", 0.5)

        self.assertEqual(packet[:8], b"/test\x00\x00\x00")
        self.assertEqual(packet[8:12], b",f\x00\x00")
        self.assertEqual(packet[12:], struct.pack(">f", 0.5))

    def test_address_must_start_with_slash(self):
        with self.assertRaisesRegex(ValueError, "starting with"):
            build_float_message("test", 1.0)

    def test_send_float_reaches_a_local_udp_receiver(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(2.0)
            host, port = receiver.getsockname()
            result = []

            def receive() -> None:
                packet, _ = receiver.recvfrom(1024)
                result.append(decode_single_float_message(packet))

            thread = threading.Thread(target=receive)
            thread.start()
            send_float(host, port, "/test", 1.0)
            thread.join(timeout=2.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [("/test", 1.0)])


if __name__ == "__main__":
    unittest.main()
