import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from oculizer.control_socket import ControlSocketServer
from oculizer.web_supervisor import WebChildSupervisor
from oculizer_web import OculizerWebServer


class FakeControl:
    def handle(self, request):
        command = request.get("command")
        if command == "telemetry":
            return {"mode": "auto", "latest_prediction": "wave", "audio_rms": 0.1}
        if command == "config-get":
            return {"revision": "abc", "path": "/config.json", "values": {}}
        if command == "config-schema":
            return {"fields": []}
        if command == "config-apply":
            return {"revision": "def", "hot_applied": list(request["changes"]), "restart_required": []}
        return request


class WebServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.temp.name) / "control.sock"
        self.control = ControlSocketServer(self.socket_path, FakeControl()).start()
        self.server = OculizerWebServer(("127.0.0.1", 0), self.socket_path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.control.stop()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        raw = None if body is None else json.dumps(body)
        actual_headers = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        if raw is not None:
            actual_headers["Content-Type"] = "application/json"
        client.request(method, path, body=raw, headers=actual_headers)
        response = client.getresponse()
        payload = response.read()
        client.close()
        return response.status, response.getheaders(), payload

    def test_serves_static_ui_with_restrictive_headers(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Oculizer", body)
        self.assertIn("default-src 'self'", dict(headers)["Content-Security-Policy"])
        self.assertIn(b"Service runtime", body)
        self.assertNotIn(b'id="target"', body)
        status, _headers, javascript = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"config-section", javascript)
        self.assertIn(b"p.changed", javascript)
        self.assertIn(b"priority_speech", javascript)
        self.assertIn(b"laneEnds", javascript)
        self.assertNotIn(b"details.open=", javascript)

    def test_status_and_config_apply_bridge_to_bounded_control_socket(self):
        status, _headers, body = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["result"]["latest_prediction"], "wave")

        status, _headers, body = self.request("POST", "/api/config/apply", {
            "expected_revision": "abc",
            "changes": {"audio.silence.duration_seconds": 1.0},
        })
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["result"]["revision"], "def")

    def test_rejects_cross_origin_and_oversized_requests(self):
        status, _headers, _body = self.request(
            "POST", "/api/control", {"command": "pause"},
            headers={"Origin": "http://other-host"},
        )
        self.assertEqual(status, 400)

        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        client.request("POST", "/api/control", headers={
            "Host": f"127.0.0.1:{self.port}", "Content-Length": "70000",
        })
        response = client.getresponse()
        response.read()
        client.close()
        self.assertEqual(response.status, 413)


class WebChildSupervisorTests(unittest.TestCase):
    @patch("oculizer.web_supervisor.subprocess.Popen")
    def test_child_crash_restarts_without_raising_into_audio_runtime(self, popen):
        first = Mock()
        first.poll.side_effect = [3]
        second = Mock()
        second.poll.return_value = None
        popen.side_effect = [first, second]
        times = iter((0.0, 1.0, 3.1))
        supervisor = WebChildSupervisor("/tmp/control.sock", clock=lambda: next(times))

        supervisor.start()
        supervisor.tick()
        supervisor.tick()
        supervisor.tick()

        self.assertEqual(popen.call_count, 2)

    @patch("oculizer.web_supervisor.subprocess.Popen")
    def test_stop_terminates_owned_child(self, popen):
        process = Mock()
        process.poll.return_value = None
        popen.return_value = process
        supervisor = WebChildSupervisor("/tmp/control.sock")
        supervisor.start()

        supervisor.stop()

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=3.0)


if __name__ == "__main__":
    unittest.main()
