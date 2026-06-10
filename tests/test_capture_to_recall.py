"""
Integration tests for the capture → embed → store → search pipeline.

These wire the real pieces together the same way the daemon does (detector,
PII redaction, real embedder, TurboQuant encode, store insert, vector cache,
combined_search) so a refactor of any stage breaks loudly here instead of
silently in production. The local VL model is NOT loaded: the AI sensitivity
layer reports "unchecked" exactly as it does on a busy daemon.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.app.search_combo import combined_search
from src.memory.embedder import Embedder
from src.memory.store import MemoryStore
from src.memory.vector_cache import VectorCache
from src.privacy.detector import SensitivityDetector
from src.privacy.patterns import redact_pii
from src.privacy.vault import Vault
from src.turboquant import encoder as tq


_EMB = Embedder.get()  # singleton; loaded once for the whole module


def _capture(store: MemoryStore, cache: VectorCache, text: str,
             app_name: str = "TestApp", source: str = "window") -> int | None:
    """Mirror the daemon's non-sensitive capture path for one text."""
    clean = redact_pii(text)
    vec = _EMB.embed(clean)
    cv = tq.encode(vec)
    mid = store.insert_memory(
        clean, source, app_name, cv, cv.residual_norm, 0,
        fp16=vec.astype(np.float16).tobytes(),
    )
    if mid is not None:
        cache.append(mid, cv, cv.residual_norm)
    return mid


class TestCaptureToRecall(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.store = MemoryStore(tmp / "m.db")
        self.cache = VectorCache(tmp / "v.npy")

    def tearDown(self):
        self._tmp.cleanup()

    def test_semantic_recall_finds_the_right_memory(self):
        mid_nn = _capture(
            self.store, self.cache,
            "Attention Is All You Need introduced the transformer architecture "
            "for neural machine translation using self-attention.",
        )
        _capture(
            self.store, self.cache,
            "Grocery list for the weekend: eggs, oat milk, spinach, coffee "
            "beans, and sourdough bread from the farmers market.",
        )
        _capture(
            self.store, self.cache,
            "Flight booking confirmation: Madison to San Francisco, departing "
            "Friday morning, seat 14C, confirmation code XK93JD.",
        )
        results = combined_search(
            "that paper about neural network attention",
            self.store, self.cache, _EMB, top_k=3,
        )
        self.assertTrue(results, "search returned nothing")
        self.assertEqual(results[0].memory_id, mid_nn)

    def test_sensitive_capture_routes_to_vault_not_search(self):
        """A capture the regex layer flags must go to the vault and never
        surface in search — the daemon's exact branch logic."""
        detector = SensitivityDetector()
        vault = Vault(self.store)
        vault.initialize("test-pass")

        text = "my social security number is 123-45-6789 please keep it safe"
        result = detector.classify(text)
        self.assertTrue(result.is_sensitive, f"reasons={result.reasons}")

        # Daemon branch: sensitive → vault, plus a sensitive stub row.
        vault.store(text, "clipboard", "Notes", result.reasons, 1.0)
        self.store.insert_sensitive(text, "clipboard", "Notes", dedup_window=1)

        self.assertEqual(len(vault.list_entries()), 1)
        results = combined_search(
            "social security number",
            self.store, self.cache, _EMB, top_k=5,
        )
        for r in results:
            self.assertNotIn("123-45-6789", r.full_text + r.text_snippet)

    def test_pii_redacted_before_storage(self):
        """Inline PII (email) is scrubbed before embed/store, and the memory
        is still findable by its surrounding content."""
        mid = _capture(
            self.store, self.cache,
            "Meeting notes with the design contractor, reach her at "
            "jane.doe@example.com about the new onboarding mockups.",
        )
        self.assertIsNotNone(mid)
        row = self.store.get_memory_by_id(mid)
        stored = (row.get("full_text") or "") + (row.get("text_snippet") or "")
        self.assertNotIn("jane.doe@example.com", stored)

        results = combined_search(
            "design contractor onboarding mockups",
            self.store, self.cache, _EMB, top_k=3,
        )
        self.assertTrue(results)
        self.assertEqual(results[0].memory_id, mid)

    def test_busy_model_marks_unchecked_not_clean(self):
        """When inference is unavailable (busy local model, no remote), the AI
        layer must report 'unchecked' (the deferred re-check contract), never
        silently 'clean'. Patched because a configured remote provider would
        otherwise really run the check on this machine."""
        from unittest import mock
        with mock.patch("src.ai.summarizer.infer_nowait", return_value=""):
            result = SensitivityDetector().classify(
                "Some perfectly ordinary text about the weather being nice today "
                "and plans to walk along the lakeshore path after lunch."
            )
        self.assertFalse(result.is_sensitive)
        self.assertTrue(result.ai_unchecked)


if __name__ == "__main__":
    unittest.main()
