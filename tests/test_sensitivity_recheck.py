"""
Tests for the deferred AI sensitivity re-check.

Regression target: the contextual AI sensitivity layer used to fail open —
``ai_is_sensitive`` returned (False, '') both when the model said "not
sensitive" and when the model was simply busy, so captures that only the AI
layer would have caught landed in the plain store and were never looked at
again. The fix makes "could not check" explicit (a None verdict), and
``recheck_sensitivity`` gives those captures a second pass: re-vault when the
model flags them, drop when the vault cannot take them (mirroring the capture
path), leave alone when clean.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.store import MemoryStore
from src.memory.vector_cache import VectorCache
from src.privacy.detector import SensitivityDetector
from src.privacy.recheck import recheck_sensitivity
from src.privacy.vault import Vault
from src.turboquant import encoder as tq


def _unit_vec(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=384).astype(np.float32)
    return v / np.linalg.norm(v)


class _Env:
    """Real store + cache + vault in a temp dir, one inserted memory."""

    def __init__(self, tmp: str, text: str) -> None:
        self.store = MemoryStore(Path(tmp) / "m.db")
        self.cache = VectorCache(Path(tmp) / "v.npy")
        self.vault = Vault(self.store)
        self.vault.initialize("test-pass")
        cv = tq.encode(_unit_vec())
        self.mid = self.store.insert_memory(
            text, "clipboard", "TestApp", cv, cv.residual_norm, 0,
        )
        assert self.mid is not None
        self.cache.append(self.mid, cv, cv.residual_norm)


class TestAiIsSensitiveThreeState(unittest.TestCase):
    """A None verdict must be distinguishable from a clean verdict."""

    TEXT = "x" * 80  # long enough to pass the min-length gate

    def test_busy_model_returns_none(self):
        with mock.patch("src.ai.summarizer.infer_nowait", return_value=""):
            from src.ai.summarizer import ai_is_sensitive
            self.assertEqual(ai_is_sensitive(self.TEXT), (None, ""))

    def test_clean_verdict(self):
        with mock.patch("src.ai.summarizer.infer_nowait", return_value="No."):
            from src.ai.summarizer import ai_is_sensitive
            self.assertEqual(ai_is_sensitive(self.TEXT), (False, ""))

    def test_sensitive_verdict_with_reason(self):
        with mock.patch(
            "src.ai.summarizer.infer_nowait",
            return_value="Yes: contains a medical diagnosis",
        ):
            from src.ai.summarizer import ai_is_sensitive
            verdict, reason = ai_is_sensitive(self.TEXT)
            self.assertTrue(verdict)
            self.assertEqual(reason, "contains a medical diagnosis")

    def test_short_text_is_clean_without_model(self):
        with mock.patch("src.ai.summarizer.infer_nowait") as m:
            from src.ai.summarizer import ai_is_sensitive
            self.assertEqual(ai_is_sensitive("short"), (False, ""))
            m.assert_not_called()


class TestDetectorUncheckedFlag(unittest.TestCase):

    BENIGN = "The weather in Madison is lovely this afternoon and the lake looks calm."

    def test_skipped_ai_sets_ai_unchecked(self):
        with mock.patch("src.ai.summarizer.ai_is_sensitive", return_value=(None, "")):
            r = SensitivityDetector().classify(self.BENIGN)
        self.assertFalse(r.is_sensitive)
        self.assertTrue(r.ai_unchecked)

    def test_clean_ai_is_not_unchecked(self):
        with mock.patch("src.ai.summarizer.ai_is_sensitive", return_value=(False, "")):
            r = SensitivityDetector().classify(self.BENIGN)
        self.assertFalse(r.is_sensitive)
        self.assertFalse(r.ai_unchecked)

    def test_ai_flag_is_sensitive(self):
        with mock.patch(
            "src.ai.summarizer.ai_is_sensitive", return_value=(True, "private note")
        ):
            r = SensitivityDetector().classify(self.BENIGN)
        self.assertTrue(r.is_sensitive)
        self.assertIn("ai_context:private note", r.reasons)
        self.assertFalse(r.ai_unchecked)

    def test_keyword_hit_short_circuits_before_ai(self):
        det = SensitivityDetector(user_keywords=["projectx"])
        with mock.patch("src.ai.summarizer.ai_is_sensitive") as m:
            r = det.classify("internal discussion about projectx roadmap and budget")
            m.assert_not_called()
        self.assertTrue(r.is_sensitive)
        self.assertFalse(r.ai_unchecked)


class TestRecheckSensitivity(unittest.TestCase):

    TEXT = "Notes from the meeting about the upcoming product launch timeline."

    def test_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _Env(tmp, self.TEXT)
            out = recheck_sensitivity(
                env.store, env.cache, env.vault, 999_999,
                classify_fn=lambda t: (True, "x"),
            )
            self.assertEqual(out, "gone")

    def test_deferred_keeps_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _Env(tmp, self.TEXT)
            out = recheck_sensitivity(
                env.store, env.cache, env.vault, env.mid,
                classify_fn=lambda t: (None, ""),
            )
            self.assertEqual(out, "deferred")
            self.assertIsNotNone(env.store.get_memory_by_id(env.mid))
            self.assertEqual(len(env.cache), 1)

    def test_clean_keeps_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _Env(tmp, self.TEXT)
            out = recheck_sensitivity(
                env.store, env.cache, env.vault, env.mid,
                classify_fn=lambda t: (False, ""),
            )
            self.assertEqual(out, "clean")
            self.assertIsNotNone(env.store.get_memory_by_id(env.mid))

    def test_flagged_moves_to_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _Env(tmp, self.TEXT)
            out = recheck_sensitivity(
                env.store, env.cache, env.vault, env.mid,
                classify_fn=lambda t: (True, "leaked credentials"),
            )
            self.assertEqual(out, "vaulted")
            self.assertIsNone(env.store.get_memory_by_id(env.mid))
            self.assertEqual(len(env.cache), 0)
            entries = env.vault.list_entries()
            self.assertEqual(len(entries), 1)

    def test_flagged_with_locked_vault_drops(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _Env(tmp, self.TEXT)
            env.vault.lock()
            out = recheck_sensitivity(
                env.store, env.cache, env.vault, env.mid,
                classify_fn=lambda t: (True, "x"),
            )
            self.assertEqual(out, "dropped")
            self.assertIsNone(env.store.get_memory_by_id(env.mid))
            self.assertEqual(len(env.vault.list_entries()), 0)


if __name__ == "__main__":
    unittest.main()
