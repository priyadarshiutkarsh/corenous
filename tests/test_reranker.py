"""Tests for the cross-encoder reranker.

Also a regression guard: the larger MiniLM-L-6 cross-encoder returns nan on CPU
under torch 2.11, so we use TinyBERT-L-2 which works on CPU. If a future model or
torch change reintroduces nan, the relevance assertion below fails loudly.
"""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.reranker import rerank_scores


class TestReranker(unittest.TestCase):
    def test_ranks_relevant_above_irrelevant_and_no_nan(self):
        scores = rerank_scores(
            "when did caroline move to San Francisco",
            ["Caroline moved to San Francisco in May 2023", "a recipe for tomato soup"],
        )
        self.assertEqual(len(scores), 2)
        self.assertFalse(bool(np.isnan(scores).any()))   # CPU nan regression guard
        self.assertGreater(float(scores[0]), float(scores[1]))

    def test_empty_docs(self):
        self.assertEqual(len(rerank_scores("anything", [])), 0)


if __name__ == "__main__":
    unittest.main()
