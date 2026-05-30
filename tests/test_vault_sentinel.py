"""
Tests for Vault passphrase verification.

Regression target: Vault.unlock used to accept ANY passphrase when the
sentinel was missing, and Vault.initialize did not write the sentinel —
so any code path that called initialize() directly (not via `vault init`)
left a vault whose unlock succeeded for every string. The fix writes the
sentinel inside initialize() and makes unlock() fail closed when the
sentinel is absent on an initialized vault.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.privacy.vault import Vault


class _FakeStore:
    """Minimal config-only stand-in for MemoryStore."""

    def __init__(self) -> None:
        self._cfg: dict[str, str] = {}

    def get_config(self, key: str):
        return self._cfg.get(key)

    def set_config(self, key: str, value: str) -> None:
        self._cfg[key] = value


class TestVaultSentinel(unittest.TestCase):

    def test_initialize_writes_sentinel(self):
        v = Vault(_FakeStore())
        v.initialize("correct horse battery staple")
        self.assertTrue(v._store.get_config("vault_sentinel_ct"))
        self.assertTrue(v._store.get_config("vault_sentinel_nonce"))

    def test_wrong_passphrase_rejected(self):
        store = _FakeStore()
        Vault(store).initialize("right-pass")
        v2 = Vault(store)
        self.assertFalse(v2.unlock("wrong-pass"))
        self.assertFalse(v2.is_unlocked())

    def test_correct_passphrase_unlocks(self):
        store = _FakeStore()
        Vault(store).initialize("right-pass")
        v2 = Vault(store)
        self.assertTrue(v2.unlock("right-pass"))
        self.assertTrue(v2.is_unlocked())

    def test_missing_sentinel_fails_closed(self):
        """An initialized vault with no sentinel must NOT accept any
        passphrase — it must raise rather than silently unlocking."""
        store = _FakeStore()
        Vault(store).initialize("right-pass")
        # Simulate a corrupt / partially-migrated vault: salt present, sentinel gone.
        store._cfg.pop("vault_sentinel_ct", None)
        store._cfg.pop("vault_sentinel_nonce", None)
        v2 = Vault(store)
        with self.assertRaises(RuntimeError):
            v2.unlock("literally anything")
        self.assertFalse(v2.is_unlocked())


if __name__ == "__main__":
    unittest.main()
