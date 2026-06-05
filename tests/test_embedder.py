"""Tests for the embedder's query/document prefix handling.

bge-v1.5 is asymmetric: the query instruction is prepended to QUERIES only, not
to stored documents. These tests verify that wiring without loading the real
model (a fake records what text actually gets encoded).
"""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.embedder import Embedder, _QUERY_PREFIX, _MODEL_NAME


class _FakeST:
    """Records the text handed to encode; returns correctly shaped zeros."""
    def __init__(self):
        self.seen = []

    def encode(self, text, normalize_embeddings=True, show_progress_bar=False, batch_size=64):
        self.seen.append(text)
        if isinstance(text, list):
            return np.zeros((len(text), 384), dtype=np.float32)
        return np.zeros(384, dtype=np.float32)


class TestEmbedderPrefix(unittest.TestCase):
    def setUp(self):
        self.e = Embedder()
        self.e._model = _FakeST()   # bypass real model load

    def test_query_gets_prefix(self):
        self.e.embed("scaling laws", is_query=True)
        self.assertEqual(self.e._model.seen[-1], _QUERY_PREFIX + "scaling laws")

    def test_document_has_no_prefix(self):
        self.e.embed("scaling laws", is_query=False)
        self.assertEqual(self.e._model.seen[-1], "scaling laws")

    def test_default_is_document(self):
        self.e.embed("plain capture")
        self.assertEqual(self.e._model.seen[-1], "plain capture")

    def test_batch_queries_all_prefixed(self):
        self.e.embed_batch(["a", "b"], is_query=True)
        self.assertEqual(self.e._model.seen[-1], [_QUERY_PREFIX + "a", _QUERY_PREFIX + "b"])

    def test_batch_documents_unprefixed(self):
        self.e.embed_batch(["a", "b"])
        self.assertEqual(self.e._model.seen[-1], ["a", "b"])

    def test_shapes(self):
        self.assertEqual(self.e.embed("x").shape, (384,))
        self.assertEqual(self.e.embed_batch(["x", "y"]).shape, (2, 384))

    def test_using_bge_model(self):
        self.assertEqual(_MODEL_NAME, "BAAI/bge-small-en-v1.5")


if __name__ == "__main__":
    unittest.main()
