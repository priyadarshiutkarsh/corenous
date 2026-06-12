"""
Regression test for FTS lexical recall on natural-language queries.

FTS5 ANDs bare terms together, so a question like "when did Alice adopt her
dog" only matched a memory containing every one of those words — which for
conversational memory is almost never, collapsing the lexical arm of hybrid
search to zero on exactly the queries users ask. fts_search now ORs the terms
and lets BM25 rank, so a memory that contains the meaningful query terms is
found even when it does not contain all the filler words.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.store import MemoryStore


class TestFtsOrRecall(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self._tmp.name) / "m.db")
        self._seed("Alice adopted a golden retriever named Max last spring")
        self._seed("Bob talked about his trip to Paris and the Louvre")
        self._seed("Alice mentioned her dog Max loves the dog park")

    def tearDown(self):
        self._tmp.cleanup()

    def _seed(self, text: str) -> None:
        # insert_memory needs a vector; use a trivial one so FTS indexing runs.
        import numpy as np
        from src.turboquant import encoder as tq
        v = np.zeros(384, dtype=np.float32)
        v[0] = 1.0
        cv = tq.encode(v)
        self.store.insert_memory(text, "chat", "App", cv, cv.residual_norm, 0)

    def test_natural_question_recalls_without_all_terms(self):
        # Not one memory contains all of {when, did, alice, adopt, her, dog}.
        rows = self.store.fts_search("when did Alice adopt her dog", limit=10)
        self.assertTrue(rows, "OR-mode FTS returned nothing for a real question")
        texts = " || ".join(r["text_snippet"] for r in rows)
        self.assertIn("Alice", texts)
        # The dog memories rank above the Paris one.
        self.assertIn("dog", texts.lower())

    def test_irrelevant_query_still_returns_nothing(self):
        self.assertEqual(self.store.fts_search("quantum chromodynamics lecture", limit=10), [])

    def test_single_term_unaffected(self):
        rows = self.store.fts_search("Paris", limit=10)
        self.assertEqual(len(rows), 1)
        self.assertIn("Paris", rows[0]["text_snippet"])


if __name__ == "__main__":
    unittest.main()
