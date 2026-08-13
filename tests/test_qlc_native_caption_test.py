import hashlib

from oculizer.light.qlc_native_caption_test import DEFAULT_KEY, _session_key


def test_empty_encryption_key_uses_qlc_default_key():
    assert _session_key("") == DEFAULT_KEY


def test_custom_encryption_key_matches_qlc_sha256_folding():
    expected = int.from_bytes(hashlib.sha256(b"ronron").digest()[:8], "big")
    assert _session_key("ronron") == expected

