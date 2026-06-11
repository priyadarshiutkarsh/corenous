"""
Tests for at-rest protection: the Keychain-bound data key and the encrypted
page-content cache.

Contract under test: cache entries written to disk are AES-256-GCM envelopes
(no plaintext content in the file), legacy plaintext entries stay readable,
and a missing key disables writes entirely rather than falling back to
plaintext.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.content_cache import ContentCache
from src.privacy.data_key import get_data_key

_KEY_HEX = "ab" * 32  # 32 bytes


class TestDataKey(unittest.TestCase):

    def test_env_override_valid(self):
        with mock.patch.dict(os.environ, {"CORENOUS_DATA_KEY": _KEY_HEX}):
            self.assertEqual(get_data_key(), bytes.fromhex(_KEY_HEX))

    def test_env_override_bad_hex_returns_none(self):
        with mock.patch.dict(os.environ, {"CORENOUS_DATA_KEY": "not-hex"}):
            self.assertIsNone(get_data_key())

    def test_env_override_wrong_length_returns_none(self):
        with mock.patch.dict(os.environ, {"CORENOUS_DATA_KEY": "abcd"}):
            self.assertIsNone(get_data_key())

    def test_keychain_hit(self):
        found = mock.Mock(returncode=0, stdout=_KEY_HEX + "\n")
        with mock.patch.dict(os.environ, {"CORENOUS_DATA_KEY": ""}):
            with mock.patch("src.privacy.data_key.subprocess.run", return_value=found):
                self.assertEqual(get_data_key(), bytes.fromhex(_KEY_HEX))

    def test_keychain_miss_creates_key(self):
        miss = mock.Mock(returncode=44, stdout="")
        added = mock.Mock(returncode=0, stdout="")
        with mock.patch.dict(os.environ, {"CORENOUS_DATA_KEY": ""}):
            with mock.patch(
                "src.privacy.data_key.subprocess.run", side_effect=[miss, added],
            ):
                key = get_data_key()
        self.assertIsNotNone(key)
        self.assertEqual(len(key), 32)

    def test_keychain_failure_returns_none(self):
        with mock.patch.dict(os.environ, {"CORENOUS_DATA_KEY": ""}):
            with mock.patch(
                "src.privacy.data_key.subprocess.run", side_effect=OSError("no security"),
            ):
                self.assertIsNone(get_data_key())


class TestContentCacheEncryption(unittest.TestCase):

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"CORENOUS_DATA_KEY": _KEY_HEX})
        self._env.start()
        self._tmp = tempfile.TemporaryDirectory()
        self.cc = ContentCache(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()
        self._env.stop()

    def test_file_on_disk_is_an_envelope_not_plaintext(self):
        self.cc.save("https://example.com/a", "Secret Plans", "the launch is friday")
        files = list(Path(self._tmp.name).rglob("*.json"))
        self.assertEqual(len(files), 1)
        raw = files[0].read_text()
        self.assertNotIn("Secret Plans", raw)
        self.assertNotIn("the launch is friday", raw)
        obj = json.loads(raw)
        self.assertEqual(obj.get("enc"), "aes-256-gcm")
        self.assertIn("ct", obj)

    def test_round_trip_via_query_recent(self):
        self.cc.save("https://example.com/a", "Title A", "alpha content here")
        out = self.cc.query_recent(days_back=1, limit=10)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Title A")
        self.assertEqual(out[0]["content"], "alpha content here")

    def test_query_domain_still_filters_by_slug(self):
        self.cc.save("https://github.com/x/y", "Repo", "readme text")
        self.cc.save("https://example.com/z", "Other", "other text")
        out = self.cc.query_domain("github", days_back=1)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Repo")

    def test_legacy_plaintext_entry_still_readable(self):
        import time as _t
        day = Path(self._tmp.name) / _t.strftime("%Y-%m-%d")
        day.mkdir(exist_ok=True)
        legacy = {"url": "u", "title": "Old", "domain": "ex", "app": "",
                  "ts": _t.time(), "content": "legacy body"}
        (day / f"{int(_t.time())}_ex_0123456789.json").write_text(json.dumps(legacy))
        out = self.cc.query_recent(days_back=1, limit=10)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Old")

    def test_no_key_means_no_write(self):
        self._env.stop()
        try:
            with mock.patch.dict(os.environ, {"CORENOUS_DATA_KEY": ""}):
                with mock.patch(
                    "src.privacy.data_key.get_data_key", return_value=None,
                ):
                    with tempfile.TemporaryDirectory() as tmp2:
                        cc2 = ContentCache(Path(tmp2))
                        cc2.save("https://example.com", "T", "body")
                        self.assertEqual(list(Path(tmp2).rglob("*.json")), [])
        finally:
            self._env.start()


if __name__ == "__main__":
    unittest.main()
