"""Tests for embedding-model drift detection and reindexing."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.reindex import model_mismatch_warning, reindex_all, _CONFIG_KEY
from src.memory.embedder import _MODEL_NAME
from src.memory.store import MemoryStore
from src.turboquant import encoder as tq


class TestModelMismatchWarning(unittest.TestCase):
    def _store(self, stored_model: str, n: int):
        s = mock.MagicMock()
        s.get_config.return_value = stored_model
        s.get_memory_count.return_value = n
        return s

    def test_same_model_no_warning(self):
        self.assertIsNone(model_mismatch_warning(self._store(_MODEL_NAME, 5)))

    def test_changed_model_warns(self):
        w = model_mismatch_warning(self._store("sentence-transformers/all-MiniLM-L6-v2", 5))
        self.assertIsNotNone(w)
        self.assertIn("reindex", w)

    def test_legacy_db_no_record_warns(self):
        w = model_mismatch_warning(self._store("", 3))
        self.assertIsNotNone(w)
        self.assertIn("reindex", w)

    def test_fresh_db_records_model_silently(self):
        s = self._store("", 0)
        self.assertIsNone(model_mismatch_warning(s))
        s.set_config.assert_called_once_with(_CONFIG_KEY, _MODEL_NAME)


class TestReindexAll(unittest.TestCase):
    def test_reindex_round_trip_and_records_model(self):
        store = MemoryStore(Path(tempfile.mkdtemp()) / "m.db")
        v = np.random.default_rng(3).standard_normal(384).astype(np.float32)
        v /= np.linalg.norm(v)
        cv = tq.encode(v)
        store.insert_memory("a real memory about vectors", "clipboard", "App",
                            cv, cv.residual_norm)
        n = reindex_all(store)              # loads the real (cached) embedder
        self.assertEqual(n, 1)
        self.assertEqual(store.get_config(_CONFIG_KEY, ""), _MODEL_NAME)
        # vector now exists and is the right shape after re-encode
        got = store.get_fp16_vectors([1])
        self.assertIn(1, got)
        self.assertEqual(len(np.frombuffer(got[1], dtype=np.float16)), 384)


if __name__ == "__main__":
    unittest.main()
