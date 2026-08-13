import hashlib
import unittest

from oculizer.light.qlc_native import DEFAULT_KEY, _session_key, parse_project_inventory


class QLCNativeTests(unittest.TestCase):
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
