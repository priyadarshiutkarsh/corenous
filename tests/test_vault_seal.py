"""
Tests for the write-only sealed vault (v2).

Regression target: `vault unlock` only ever unlocked the CLI's own process,
so the capture daemon's vault was permanently locked and every regex-flagged sensitive
capture was silently dropped. seal() encrypts to the vault's PUBLIC key with
no session secret, so the daemon can protect captures while the vault stays
locked; only the passphrase can read them back.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.store import MemoryStore
from src.privacy.vault import Vault, VaultLockedError
from src.privacy.recheck import recheck_sensitivity
from src.memory.vector_cache import VectorCache
from src.turboquant import encoder as tq

import numpy as np


def _store(tmp: str) -> MemoryStore:
    return MemoryStore(Path(tmp) / "m.db")


class TestSealedVault(unittest.TestCase):

    def test_seal_works_while_locked_and_reads_require_passphrase(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            Vault(store).initialize("pass-phrase-1")  # init in "another process"

            # A fresh Vault instance (the daemon): locked, but can seal.
            daemon_vault = Vault(store)
            self.assertFalse(daemon_vault.is_unlocked())
            self.assertTrue(daemon_vault.can_seal())
            vid = daemon_vault.seal("api key hunter2", "clipboard", "Notes",
                                    ["password_field"], 123.0)
            self.assertGreater(vid, 0)

            # The sealer itself cannot read it back.
            with self.assertRaises(VaultLockedError):
                daemon_vault.retrieve(vid)

            # The passphrase can.
            reader = Vault(store)
            self.assertTrue(reader.unlock("pass-phrase-1"))
            data = reader.retrieve(vid)
            self.assertEqual(data["text"], "api key hunter2")
            self.assertEqual(data["reasons"], ["password_field"])

    def test_wrong_passphrase_cannot_read_sealed_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            Vault(store).initialize("right-pass")
            Vault(store).seal("secret", "clipboard", "App", ["x"], 1.0)
            v = Vault(store)
            self.assertFalse(v.unlock("wrong-pass"))
            with self.assertRaises(VaultLockedError):
                v.retrieve(1)

    def test_legacy_symmetric_entry_still_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            v = Vault(store)
            v.initialize("pw-legacy-12")
            vid = v.store("old style entry", "clipboard", "App", ["k"], 2.0)
            v2 = Vault(store)
            self.assertTrue(v2.unlock("pw-legacy-12"))
            self.assertEqual(v2.retrieve(vid)["text"], "old style entry")

    def test_legacy_vault_upgrades_on_unlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            v = Vault(store)
            v.initialize("pw-upgrade-1")
            # Simulate a vault created before sealing existed.
            for k in ("vault_seal_pub", "vault_seal_priv_wrapped",
                      "vault_seal_priv_nonce"):
                store.set_config(k, "")
            fresh = Vault(store)
            self.assertFalse(fresh.can_seal())
            self.assertTrue(fresh.unlock("pw-upgrade-1"))
            self.assertTrue(fresh.can_seal())
            # And sealed entries written post-upgrade decrypt.
            vid = Vault(store).seal("post upgrade", "s", "a", [], 3.0)
            self.assertEqual(fresh.retrieve(vid)["text"], "post upgrade")

    def test_recheck_seals_with_locked_vault(self):
        """The deferred sensitivity re-check no longer needs an unlocked vault."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            Vault(store).initialize("pw-recheck-1")
            cache = VectorCache(Path(tmp) / "v.npy")
            rng = np.random.default_rng(7)
            vec = rng.normal(size=384).astype(np.float32)
            vec /= np.linalg.norm(vec)
            cv = tq.encode(vec)
            mid = store.insert_memory("private medical note", "window", "App",
                                      cv, cv.residual_norm, 0)
            cache.append(mid, cv, cv.residual_norm)

            locked_vault = Vault(store)  # daemon's instance: never unlocked
            out = recheck_sensitivity(
                store, cache, locked_vault, mid,
                classify_fn=lambda t: (True, "medical"),
            )
            self.assertEqual(out, "vaulted")
            self.assertIsNone(store.get_memory_by_id(mid))
            self.assertEqual(len(locked_vault.list_entries()), 1)


if __name__ == "__main__":
    unittest.main()
