"""
Tests for the int8 Stage-1 search index.

The resident index is quantized int8 (per-row max-abs) instead of float32 to
keep RAM ~4x smaller. These tests assert the quantization stays faithful: the
top result is unchanged, top-k overlap is high, and add/remove/replace keep the
quantized arrays consistent.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.vector_cache import VectorCache
from src.turboquant import encoder as tq


def _unit(rng, n):
    v = rng.normal(size=(n, 384)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def _cache(vecs) -> VectorCache:
    c = VectorCache(Path(tempfile.mkdtemp()) / "v.npy")
    entries = []
    for i, v in enumerate(vecs):
        cv = tq.encode(v)
        entries.append((i, cv, cv.residual_norm))
    c.load_from_store(entries)
    return c


class TestInt8Index(unittest.TestCase):

    def setUp(self):
        self.rng = np.random.default_rng(1)
        self.vecs = _unit(self.rng, 400)
        self.cache = _cache(self.vecs)

    def test_index_is_int8(self):
        self.assertEqual(self.cache._stage1_q8.dtype, np.int8)
        self.assertEqual(self.cache._stage1_scale.dtype, np.float32)

    def test_index_bytes_far_smaller_than_float32(self):
        n = len(self.vecs)
        f32_stage1 = n * 384 * 4
        # int8 stage1 (384) + scale (4) + qjl signs (64) per memory.
        self.assertLess(self.cache.index_bytes(), f32_stage1)
        self.assertLessEqual(self.cache._stage1_q8.nbytes, n * 384 + 8)

    def test_top1_matches_float32_reference(self):
        ref_mat = np.vstack([tq.decode(cv) for cv in self.cache._cvs])
        for qi in (0, 50, 199, 300):
            q = tq.encode(self.vecs[qi])
            ref = ref_mat @ tq.decode(q)
            got = self.cache.scores(q, coarse=True)
            self.assertEqual(int(np.argmax(ref)), int(np.argmax(got)))

    def test_topk_overlap_high(self):
        ref_mat = np.vstack([tq.decode(cv) for cv in self.cache._cvs])
        overlaps = []
        for qi in range(0, 400, 40):
            q = tq.encode(self.vecs[qi])
            ref = set(np.argsort(ref_mat @ tq.decode(q))[::-1][:20])
            got = set(np.argsort(self.cache.scores(q, coarse=True))[::-1][:20])
            overlaps.append(len(ref & got) / 20.0)
        self.assertGreaterEqual(np.mean(overlaps), 0.9)

    def test_remove_keeps_index_consistent(self):
        before = len(self.cache)
        self.assertTrue(self.cache.remove(10))
        q = tq.encode(self.vecs[0])
        scores = self.cache.scores(q, coarse=True)
        self.assertEqual(len(scores), before - 1)
        self.assertEqual(self.cache._stage1_q8.shape[0], before - 1)

    def test_append_rebuilds_index(self):
        new_v = _unit(self.rng, 1)[0]
        cv = tq.encode(new_v)
        self.cache.append(9999, cv, cv.residual_norm)
        scores = self.cache.scores(tq.encode(new_v), coarse=True)
        self.assertEqual(len(scores), 401)
        # The freshly appended vector should score near the top against itself.
        self.assertEqual(self.cache.memory_ids()[int(np.argmax(scores))], 9999)

    def test_empty_cache_scores_empty(self):
        c = VectorCache(Path(tempfile.mkdtemp()) / "v.npy")
        self.assertEqual(len(c.scores(tq.encode(self.vecs[0]))), 0)


if __name__ == "__main__":
    unittest.main()
