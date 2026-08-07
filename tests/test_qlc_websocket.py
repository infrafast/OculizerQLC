import unittest
from unittest.mock import Mock

from oculizer.light.qlc_websocket import (
    QLCWebSocketClient,
    QLCWebSocketConfig,
    QLCWebSocketError,
    parse_button_inventory,
)


def inventory(*children):
    return {"pages": [{"id": 1, "type": "Frame", "children": list(children)}]}


class FakeSocket:
    def __init__(self, replies=()):
        self.replies = list(replies)
        self.sent = []
        self.closed = False

    def send(self, message):
        self.sent.append(message)

    def recv(self):
        if not self.replies:
            raise TimeoutError("no reply")
        return self.replies.pop(0)

    def close(self):
        self.closed = True


class InventoryTests(unittest.TestCase):
    def test_parses_nested_toggle_buttons_and_ignores_other_widgets(self):
        buttons = parse_button_inventory(inventory(
            {"id": 2, "type": "Button", "caption": "Party", "actionType": 0},
            {"id": 3, "type": "Slider", "caption": "Master"},
        ))
        self.assertEqual(buttons["party"].widget_id, 2)

    def test_rejects_duplicates_and_malformed_json(self):
        cases = (
            inventory(
                {"id": 2, "type": "Button", "caption": "Party", "actionType": 0},
                {"id": 3, "type": "Button", "caption": "Party", "actionType": 0},
            ),
            {"widgets": []},
            "not-json",
        )
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(QLCWebSocketError):
                parse_button_inventory(payload)

    def test_uses_language_independent_type_id(self):
        buttons = parse_button_inventory(inventory(
            {"id": 2, "typeId": 1, "type": "Bouton", "caption": "Party", "actionType": 0}
        ))
        self.assertIn("party", buttons)

    def test_normalizes_case_spaces_underscores_and_hyphens(self):
        buttons = parse_button_inventory(inventory(
            {"id": 2, "typeId": 1, "caption": "WHITE FAIRIES", "actionType": 0}
        ))
        self.assertEqual(buttons["whitefairies"].widget_id, 2)

    def test_rejects_captions_ambiguous_after_normalization(self):
        with self.assertRaisesRegex(QLCWebSocketError, "Ambiguous"):
            parse_button_inventory(inventory(
                {"id": 2, "typeId": 1, "caption": "AMBIENT 1", "actionType": 0},
                {"id": 3, "typeId": 1, "caption": "ambient_1", "actionType": 0},
            ))


class ClientTests(unittest.TestCase):
    def test_queries_status_and_sends_one_press_without_release(self):
        socket = FakeSocket(["99|BUTTON|0", "QLC+API|getWidgetStatus|2|0"])
        factory = Mock(return_value=socket)
        client = QLCWebSocketClient(
            QLCWebSocketConfig(), websocket_factory=factory,
            inventory_loader=lambda: inventory(
                {"id": 2, "type": "Button", "caption": "Party", "actionType": 0}
            ),
        )
        client.connect()

        self.assertTrue(client.activate_button("Party"))
        self.assertEqual(socket.sent, ["QLC+API|getWidgetStatus|2", "2|255"])
        client.close()
        self.assertTrue(socket.closed)

    def test_active_or_monitoring_button_is_not_toggled_off(self):
        for state in (127, 255):
            with self.subTest(state=state):
                socket = FakeSocket([f"QLC+API|getWidgetStatus|2|{state}"])
                client = QLCWebSocketClient(
                    QLCWebSocketConfig(), websocket_factory=Mock(return_value=socket),
                    inventory_loader=lambda: inventory(
                        {"id": 2, "type": "Button", "caption": "Party", "actionType": 0}
                    ),
                )
                client.connect()
                self.assertTrue(client.activate_button("Party"))
                self.assertEqual(socket.sent, ["QLC+API|getWidgetStatus|2"])

    def test_blackout_button_can_be_activated_when_explicitly_allowed(self):
        socket = FakeSocket(["QLC+API|getWidgetStatus|5|0"])
        client = QLCWebSocketClient(
            QLCWebSocketConfig(), websocket_factory=Mock(return_value=socket),
            inventory_loader=lambda: inventory(
                {"id": 5, "type": "Button", "caption": "off", "actionType": 2}
            ),
        )
        client.connect()

        self.assertTrue(client.activate_button("off", allowed_action_types=(2,)))
        self.assertEqual(socket.sent, ["QLC+API|getWidgetStatus|5", "5|255"])

    def test_stop_all_button_is_sent_as_one_momentary_press(self):
        socket = FakeSocket()
        client = QLCWebSocketClient(
            QLCWebSocketConfig(), websocket_factory=Mock(return_value=socket),
            inventory_loader=lambda: inventory(
                {"id": 6, "type": "Button", "caption": "off", "actionType": 3}
            ),
        )
        client.connect()

        self.assertTrue(client.activate_button("off", allowed_action_types=(3,)))
        self.assertEqual(socket.sent, ["6|255"])

    def test_dry_run_opens_no_socket(self):
        factory = Mock(side_effect=AssertionError("must not connect"))
        client = QLCWebSocketClient(
            QLCWebSocketConfig(dry_run=True), websocket_factory=factory,
        )
        client.connect()
        client.validate_captions(["Party"])
        self.assertTrue(client.activate_button("Party"))
        factory.assert_not_called()

    def test_connection_failure_is_explicit(self):
        client = QLCWebSocketClient(
            QLCWebSocketConfig(),
            websocket_factory=Mock(side_effect=OSError("refused")),
            inventory_loader=lambda: inventory(),
        )
        with self.assertRaisesRegex(QLCWebSocketError, "Cannot connect"):
            client.connect()

    def test_missing_caption_fails_explicitly(self):
        client = QLCWebSocketClient(
            QLCWebSocketConfig(), websocket_factory=Mock(return_value=FakeSocket()),
            inventory_loader=lambda: inventory(),
        )
        client.connect()
        with self.assertRaisesRegex(QLCWebSocketError, "not found"):
            client.validate_captions(["Party"])

    def test_configured_non_toggle_button_fails_explicitly(self):
        client = QLCWebSocketClient(
            QLCWebSocketConfig(), websocket_factory=Mock(return_value=FakeSocket()),
            inventory_loader=lambda: inventory(
                {"id": 2, "type": "Button", "caption": "Flash", "actionType": 1}
            ),
        )
        client.connect()
        with self.assertRaisesRegex(QLCWebSocketError, "Toggle Function"):
            client.validate_captions(["Flash"])


if __name__ == "__main__":
    unittest.main()
