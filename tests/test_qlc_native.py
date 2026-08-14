import hashlib
import unittest
from unittest.mock import patch

from oculizer.light.qlc_native import (
    DEFAULT_KEY,
    NativeState,
    QLCNativeClient,
    _session_key,
    parse_project_inventory,
)


class QLCNativeTests(unittest.TestCase):
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
        buttons, sliders = parse_project_inventory(b'''<Workspace><VirtualConsole>
          <Frame ID="1" Caption="Scenes"><Button ID="71" Caption="Party" /></Frame>
          <Slider ID="72" Caption="Master"><Value Low="10" High="210" /></Slider>
        </VirtualConsole></Workspace>''')
        self.assertEqual(buttons["party"].widget_id, 71)
        self.assertEqual(sliders["master"].widget_id, 72)
        self.assertEqual((sliders["master"].low, sliders["master"].high), (10.0, 210.0))


if __name__ == "__main__":
    unittest.main()
