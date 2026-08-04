import json
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path

from oculizer.light.osc_client import (
    OscClient,
    OscConfig,
    OscConfigError,
    build_float_message,
    clamp_level,
)


def decode_single_float_message(packet: bytes) -> tuple[str, float]:
    address_end = packet.index(b"\x00")
    address = packet[:address_end].decode("utf-8")
    type_start = (address_end + 4) & ~3
    type_end = packet.index(b"\x00", type_start)
    value_start = (type_end + 4) & ~3
    if packet[type_start:type_end] != b",f":
        raise ValueError("Unexpected OSC type tag")
    return address, struct.unpack(">f", packet[value_start : value_start + 4])[0]


class OscConfigTests(unittest.TestCase):
    def test_loads_configuration_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "osc.json"
            path.write_text(
                json.dumps(
                    {
                        "host": "192.0.2.10",
                        "port": 7777,
                        "dry_run": True,
                        "paths": {"blackout": "/show/blackout"},
                    }
                ),
                encoding="utf-8",
            )

            config = OscConfig.from_file(path)

        self.assertEqual(config.host, "192.0.2.10")
        self.assertEqual(config.port, 7777)
        self.assertTrue(config.dry_run)
        self.assertEqual(config.blackout_path, "/show/blackout")

    def test_rejects_invalid_configuration(self):
        with self.assertRaisesRegex(OscConfigError, "port"):
            OscConfig.from_mapping({"port": 0})
        with self.assertRaisesRegex(OscConfigError, "dry_run"):
            OscConfig.from_mapping({"dry_run": "yes"})
        with self.assertRaisesRegex(OscConfigError, "blackout"):
            OscConfig.from_mapping({"paths": {"blackout": "blackout"}})


class OscEncodingTests(unittest.TestCase):
    def test_builds_standard_float_message(self):
        packet = build_float_message("/oculizer/master", 0.5)
        self.assertEqual(decode_single_float_message(packet), ("/oculizer/master", 0.5))

    def test_clamps_levels_and_rejects_non_finite_values(self):
        self.assertEqual(clamp_level(-1), 0.0)
        self.assertEqual(clamp_level(2), 1.0)
        self.assertEqual(clamp_level(0.25), 0.25)
        for value in (float("nan"), float("inf"), "not-a-number"):
            with self.assertRaises(ValueError):
                clamp_level(value)


class OscClientTests(unittest.TestCase):
    def test_dry_run_does_not_create_a_socket(self):
        client = OscClient(OscConfig(dry_run=True))
        self.assertIsNone(client._socket)
        self.assertTrue(client.press("/test"))
        self.assertTrue(client.release("/test"))
        client.close()
        client.close()
        self.assertTrue(client.closed)

    def test_send_after_close_is_rejected(self):
        client = OscClient(OscConfig(dry_run=True))
        client.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            client.send("/test", 1.0)

    def test_dry_run_exact_path_filter_suppresses_only_selected_logs(self):
        client = OscClient(
            OscConfig(dry_run=True),
            log_filter_paths=["/oculizer/bass", "/oculizer/mid"],
        )
        with self.assertLogs("oculizer.light.osc_client", level="INFO") as captured:
            self.assertTrue(client.send("/oculizer/bass", 0.5))
            self.assertTrue(client.send("/oculizer/master", 0.75))

        output = "\n".join(captured.output)
        self.assertNotIn("/oculizer/bass", output)
        self.assertIn("/oculizer/master", output)

    def test_rejects_invalid_log_filter_path(self):
        with self.assertRaisesRegex(ValueError, "log-filter path"):
            OscClient(OscConfig(dry_run=True), log_filter_paths=["oculizer/bass"])

    def test_sends_press_release_level_and_blackout_over_udp(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(2.0)
            host, port = receiver.getsockname()
            received = []

            def receive() -> None:
                for _ in range(4):
                    packet, _ = receiver.recvfrom(1024)
                    received.append(decode_single_float_message(packet))

            thread = threading.Thread(target=receive)
            thread.start()
            with OscClient(
                OscConfig(
                    host=host,
                    port=port,
                    blackout_path="/oculizer/system/blackout",
                )
            ) as client:
                client.press("/oculizer/scenes/party")
                client.release("/oculizer/scenes/party")
                client.set_level("/oculizer/master", 2.0)
                client.blackout(True)
            thread.join(timeout=2.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(
            received,
            [
                ("/oculizer/scenes/party", 1.0),
                ("/oculizer/scenes/party", 0.0),
                ("/oculizer/master", 1.0),
                ("/oculizer/system/blackout", 1.0),
            ],
        )


if __name__ == "__main__":
    unittest.main()
