"""
Tests for age-based memory retention (store.prune_older_than).

Retention deletes old memories and their vectors but, unlike a user delete,
does NOT tombstone them — aged-out content should be re-capturable later.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.store import MemoryStore
from src.turboquant import encoder as tq


def _cv():
    v = np.zeros(384, dtype=np.float32)
    v[0] = 1.0
    return tq.encode(v)


class TestRetention(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self._tmp.name) / "m.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _insert(self, text: str, age_days: float) -> int:
        cv = _cv()
        mid = self.store.insert_memory(text, "chat", "App", cv, cv.residual_norm, 0)
        ts = time.time() - age_days * 86400.0
        self.store._conn.execute(
            "UPDATE memories SET created_at = ? WHERE id = ?", (ts, mid))
        self.store._conn.commit()
        return mid

    def test_prunes_only_old_memories(self):
        old1 = self._insert("ancient one", 40)
        old2 = self._insert("ancient two", 31)
        fresh = self._insert("recent", 2)
        cutoff = time.time() - 30 * 86400.0

        pruned = set(self.store.prune_older_than(cutoff))
        self.assertEqual(pruned, {old1, old2})
        self.assertIsNone(self.store.get_memory_by_id(old1))
        self.assertIsNone(self.store.get_memory_by_id(old2))
        self.assertIsNotNone(self.store.get_memory_by_id(fresh))

    def test_vectors_are_purged(self):
        old = self._insert("old with vector", 50)
        cutoff = time.time() - 30 * 86400.0
        self.store.prune_older_than(cutoff)
        self.assertEqual(self.store.get_fp16_vectors([old]), {})
        rows = self.store._conn.execute(
            "SELECT COUNT(*) c FROM vectors WHERE memory_id = ?", (old,)
        ).fetchone()
        self.assertEqual(rows["c"], 0)

    def test_no_tombstone_so_content_recapturable(self):
        old = self._insert("re-capturable text", 40)
        chash = self.store._conn.execute(
            "SELECT content_hash FROM memories WHERE id = ?", (old,)
        ).fetchone()["content_hash"]
        self.store.prune_older_than(time.time() - 30 * 86400.0)
        self.assertFalse(self.store.is_hash_deleted(chash))

    def test_nothing_to_prune_returns_empty(self):
        self._insert("fresh", 1)
        self.assertEqual(self.store.prune_older_than(time.time() - 30 * 86400.0), [])


if __name__ == "__main__":
    unittest.main()
