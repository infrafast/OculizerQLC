import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from oculizer.control_socket import ControlSocketServer, send_control_request


class EchoControl:
    def handle(self, request):
        if request.get("command") == "fail":
            raise ValueError("bad command")
        return request


class ControlSocketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "control.sock"
        self.server = None

    def tearDown(self):
        if self.server is not None:
            self.server.stop()
        self.temp.cleanup()

    def test_round_trip_and_owner_only_permissions(self):
        self.server = ControlSocketServer(self.path, EchoControl()).start()

        result = send_control_request(self.path, {"command": "status"})

        self.assertEqual(result, {"command": "status"})
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_errors_are_acknowledged(self):
        self.server = ControlSocketServer(self.path, EchoControl()).start()

        with self.assertRaisesRegex(RuntimeError, "bad command"):
            send_control_request(self.path, {"command": "fail"})

    def test_stale_socket_is_recovered_and_removed_on_stop(self):
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(self.path))
        stale.close()

        self.server = ControlSocketServer(self.path, EchoControl()).start()
        self.assertTrue(self.path.exists())
        self.server.stop()
        self.server = None
        self.assertFalse(self.path.exists())

    def test_second_server_cannot_steal_active_socket(self):
        self.server = ControlSocketServer(self.path, EchoControl()).start()

        with self.assertRaisesRegex(RuntimeError, "already active"):
            ControlSocketServer(self.path, EchoControl()).start()

    def test_regular_file_at_socket_path_is_never_deleted(self):
        self.path.write_text("keep me")

        with self.assertRaisesRegex(RuntimeError, "not a socket"):
            ControlSocketServer(self.path, EchoControl()).start()

        self.assertEqual(self.path.read_text(), "keep me")

    def test_malformed_json_returns_error_response(self):
        self.server = ControlSocketServer(self.path, EchoControl()).start()
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(self.path))
        client.sendall(b"not-json\n")
        response = b""
        while not response.endswith(b"\n"):
            response += client.recv(4096)
        client.close()

        decoded = json.loads(response)
        self.assertFalse(decoded["ok"])

    def test_concurrent_clients_receive_independent_acknowledgements(self):
        self.server = ControlSocketServer(self.path, EchoControl()).start()

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(
                lambda index: send_control_request(self.path, {"command": "status", "index": index}),
                range(20),
            ))

        self.assertEqual({result["index"] for result in results}, set(range(20)))
