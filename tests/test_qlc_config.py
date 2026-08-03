import json
import tempfile
import unittest
from pathlib import Path

from oculizer.light.qlc_config import QLCConfig, QLCConfigError


class QLCConfigTests(unittest.TestCase):
    def test_loads_transport_controls_and_routing_from_one_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qlc_config.json"
            path.write_text(
                json.dumps({
                    "transport": {"host": "192.0.2.10", "port": 7777, "dry_run": True},
                    "controls": {"blackout": "/blackout", "master": "/oculizer/master", "bass": "/oculizer/bass"},
                    "routing": {
                        "pulse_seconds": 0.2,
                        "scenes": {
                            "announcement": {
                                "action": "toggle",
                                "path": "/oculizer/scenes/announcement",
                            },
                            "off": {"action": "off"},
                        },
                    },
                }),
                encoding="utf-8",
            )
            config = QLCConfig.from_file(path)

        self.assertEqual(config.transport.host, "192.0.2.10")
        self.assertEqual(config.transport.port, 7777)
        self.assertTrue(config.transport.dry_run)
        self.assertEqual(config.transport.blackout_path, "/blackout")
        self.assertEqual(config.controls["master"], "/oculizer/master")
        self.assertEqual(config.controls["bass"], "/oculizer/bass")
        self.assertEqual(config.routing.pulse_seconds, 0.2)
        self.assertEqual(config.routing.get("announcement").path, "/oculizer/scenes/announcement")
        self.assertEqual(config.routing.get("off").action, "off")

    def test_rejects_invalid_sections_and_controls(self):
        invalid_configs = (
            {"transport": []},
            {"controls": []},
            {"routing": []},
            {"controls": {"blackout": "blackout"}},
            {"routing": {"scenes": {"party": {"path": "party"}}}},
        )
        for data in invalid_configs:
            with self.subTest(data=data), self.assertRaises(QLCConfigError):
                QLCConfig.from_mapping(data)


if __name__ == "__main__":
    unittest.main()
