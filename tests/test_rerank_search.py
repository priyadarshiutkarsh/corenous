"""Tests for the fp16 re-rank stage.

The 58-byte TurboQuant code is a coarse filter; the search re-ranks the coarse
candidates against full-precision float16 vectors stored on disk. These tests
lock in the storage round-trip, backward compatibility with pre-migration
memories that have no fp16, and the end-to-end behaviour that the exact nearest
neighbour wins after re-rank. The recall *gain* itself is measured separately by
scripts/benchmark.py.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.store import MemoryStore
from src.memory.vector_cache import VectorCache
from src.app.search_combo import combined_search
from src.turboquant import encoder as tq


def _new_store() -> MemoryStore:
    d = Path(tempfile.mkdtemp())
    return MemoryStore(d / "memories.db")


def _unit(rng) -> np.ndarray:
    x = rng.standard_normal(384).astype(np.float32)
    return x / np.linalg.norm(x)


class _FakeEmbedder:
    """Returns a fixed query vector regardless of the query string, so the test
    isolates the vector ranker from FTS/metadata lexical matching."""
    def __init__(self, vec: np.ndarray) -> None:
        self._vec = vec

    def embed(self, _text: str) -> np.ndarray:
        return self._vec


class TestFp16Storage(unittest.TestCase):
    def test_fp16_roundtrips_through_store(self):
        store = _new_store()
        v = _unit(np.random.default_rng(1))
        cv = tq.encode(v)
        mid = store.insert_memory(
            "document one", "clipboard", "App", cv, cv.residual_norm,
            fp16=v.astype(np.float16).tobytes(),
        )
        got = store.get_fp16_vectors([mid])
        self.assertIn(mid, got)
        decoded = np.frombuffer(got[mid], dtype=np.float16).astype(np.float32)
        # float16 preserves cosine to better than 0.999.
        self.assertGreater(float(decoded @ v), 0.999)

    def test_memory_without_fp16_is_absent_not_an_error(self):
        store = _new_store()
        v = _unit(np.random.default_rng(2))
        cv = tq.encode(v)
        mid = store.insert_memory(
            "document two", "clipboard", "App", cv, cv.residual_norm,
        )
        self.assertEqual(store.get_fp16_vectors([mid]), {})

    def test_update_memory_vector_writes_fp16(self):
        store = _new_store()
        v0 = _unit(np.random.default_rng(3))
        cv0 = tq.encode(v0)
        mid = store.insert_memory("doc", "clipboard", "App", cv0, cv0.residual_norm)
        v1 = _unit(np.random.default_rng(4))
        cv1 = tq.encode(v1)
        store.update_memory_vector(mid, cv1, cv1.residual_norm,
                                   fp16=v1.astype(np.float16).tobytes())
        decoded = np.frombuffer(store.get_fp16_vectors([mid])[mid],
                                dtype=np.float16).astype(np.float32)
        self.assertGreater(float(decoded @ v1), 0.999)


class TestRerankSearch(unittest.TestCase):
    def test_exact_nearest_neighbour_ranks_first(self):
        store = _new_store()
        rng = np.random.default_rng(7)
        vecs = [_unit(rng) for _ in range(6)]
        mids = []
        for i, x in enumerate(vecs):
            cv = tq.encode(x)
            mid = store.insert_memory(
                f"document number {i}", "clipboard", "App", cv, cv.residual_norm,
                window_title=f"window {i}",
                fp16=x.astype(np.float16).tobytes(),
            )
            mids.append(mid)

        cache = VectorCache(Path(tempfile.mkdtemp()) / "vectors.npy")
        cache.load_from_store(store.get_all_compressed_vectors())

        target = 3
        # Gibberish query string so FTS / metadata contribute nothing; the fake
        # embedder feeds the target's own vector as the query embedding.
        results = combined_search("zzqqxx", store, cache, _FakeEmbedder(vecs[target]), top_k=6)
        self.assertTrue(results)
        self.assertEqual(results[0].memory_id, mids[target])

    def test_search_still_works_without_fp16(self):
        store = _new_store()
        rng = np.random.default_rng(8)
        vecs = [_unit(rng) for _ in range(4)]
        mids = []
        for i, x in enumerate(vecs):
            cv = tq.encode(x)
            mid = store.insert_memory(
                f"legacy doc {i}", "clipboard", "App", cv, cv.residual_norm,
            )  # no fp16 -> pre-migration memory
            mids.append(mid)

        cache = VectorCache(Path(tempfile.mkdtemp()) / "vectors.npy")
        cache.load_from_store(store.get_all_compressed_vectors())

        target = 1
        results = combined_search("zzqqxx", store, cache, _FakeEmbedder(vecs[target]), top_k=4)
        self.assertTrue(results)
        self.assertEqual(results[0].memory_id, mids[target])


if __name__ == "__main__":
    unittest.main()
