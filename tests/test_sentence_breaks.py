"""
Tests for sentence-boundary repair helpers in src/memory/summaries.py.

The local Llama 3.2 3B occasionally produces run-on text where periods
between sentences are dropped — these helpers restore the structure
without re-prompting the model.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.summaries import normalize_sentence_breaks, split_run_on_bullet


# ── normalize_sentence_breaks ────────────────────────────────────────────

class TestNormalizeSentenceBreaks(unittest.TestCase):

    def test_inserts_period_before_The(self):
        out = normalize_sentence_breaks("rerank The update includes")
        self.assertEqual(out, "rerank. The update includes")

    def test_inserts_period_before_This(self):
        out = normalize_sentence_breaks("matters This kicks off the run")
        self.assertEqual(out, "matters. This kicks off the run")

    def test_inserts_period_before_However(self):
        out = normalize_sentence_breaks("works However the cache stalls")
        self.assertEqual(out, "works. However the cache stalls")

    def test_handles_multiple_breaks_in_one_string(self):
        bad = "a The b The c The d"
        out = normalize_sentence_breaks(bad)
        # Each "X The y" pair should get a period.
        self.assertEqual(out.count(". The"), 3)

    def test_does_not_split_capitalized_continuation(self):
        """'I like The Beatles' is a band name — the third word is itself
        capitalized, so this is a title, not a sentence boundary."""
        out = normalize_sentence_breaks("I like The Beatles")
        self.assertEqual(out, "I like The Beatles")

    def test_does_not_split_unknown_proper_noun(self):
        """Proper nouns not in the starter list must not trigger a split,
        otherwise 'used GitHub today' becomes 'used. GitHub today'."""
        out = normalize_sentence_breaks("used GitHub today and left")
        self.assertEqual(out, "used GitHub today and left")

    def test_does_not_touch_already_punctuated_text(self):
        good = "The update worked. The system is fine."
        self.assertEqual(normalize_sentence_breaks(good), good)

    def test_empty_and_none_safe(self):
        self.assertEqual(normalize_sentence_breaks(""), "")
        self.assertEqual(normalize_sentence_breaks(None), None)  # type: ignore[arg-type]

    def test_word_boundary_required_on_left(self):
        """The starter must be preceded by a real word, not be word-internal.
        'theThe' must not match as 'the' + 'The'."""
        out = normalize_sentence_breaks("worktheThe update")
        # The 'the' inside 'workthe' should not anchor a sentence break.
        # Only the leading 'theThe' pattern is questionable. Either way the
        # word boundary must prevent splitting INSIDE a word.
        self.assertNotIn("workthe. The", out)


# ── split_run_on_bullet ──────────────────────────────────────────────────

class TestSplitRunOnBullet(unittest.TestCase):

    def test_splits_screenshot_run_on_into_sentences(self):
        """The exact bullet from the bug report screenshot must split into
        four cleanly-terminated sentences."""
        bad = (
            "• Updated the screenpipe CLI binary to enable semantic search "
            "via FTS5 and post rerank The update includes corenous, a "
            "macOS only version with real native APIs Screenpipe now uses "
            "a local LLM for heading and subject refinement The new version "
            "includes TurboQuant vector compression and browser tab + window "
            "text capture The update is now live, and the new version is "
            "version 4.7.0"
        )
        out = split_run_on_bullet(bad)
        # Expect ≥4 separate bullets (the "APIs Screenpipe" boundary is
        # intentionally NOT detected — proper-noun splits are too risky).
        self.assertGreaterEqual(len(out), 4)
        for line in out:
            self.assertTrue(line.startswith("• "))
            self.assertTrue(
                line.rstrip().endswith((".", "!", "?")),
                f"bullet must end with terminal punctuation: {line!r}",
            )

    def test_preserves_single_clean_sentence(self):
        out = split_run_on_bullet("• Updated the CLI binary to enable search.")
        self.assertEqual(out, ["• Updated the CLI binary to enable search."])

    def test_appends_period_to_unterminated_bullet(self):
        out = split_run_on_bullet("• A bullet without a period")
        self.assertEqual(out, ["• A bullet without a period."])

    def test_returns_empty_for_empty_input(self):
        self.assertEqual(split_run_on_bullet(""), [])
        self.assertEqual(split_run_on_bullet("•   "), [])

    def test_strips_alternative_bullet_glyphs(self):
        """The function should accept lines starting with hyphen or asterisk
        too, since the LLM sometimes uses those instead of •."""
        out = split_run_on_bullet("- A point. Another point.")
        self.assertEqual(len(out), 2)
        self.assertTrue(out[0].startswith("• "))

    def test_does_not_split_title_with_capitalized_continuation(self):
        """'I like The Beatles' should stay as one bullet."""
        out = split_run_on_bullet("• I like The Beatles")
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
