"""
Tests for is_heading_paraphrase — token overlap detection for bullets
that paraphrase the memory heading.

The existing dedup in overlay.py catches exact substring overlap.
Paraphrases (inserted articles, reordered words, slight rewording)
slip through that check, so the model effectively wastes one bullet
restating what the heading already says. This token overlap rule
catches the paraphrased case.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.summaries import is_heading_paraphrase


class TestIsHeadingParaphrase(unittest.TestCase):

    # ── True positives: real paraphrases ────────────────────────────

    def test_exact_match_is_paraphrase(self):
        self.assertTrue(is_heading_paraphrase(
            "Updated CLI binary for screenpipe search",
            "Updated CLI binary for screenpipe search",
        ))

    def test_inserted_article_is_paraphrase(self):
        self.assertTrue(is_heading_paraphrase(
            "Updated the CLI binary for screenpipe search",
            "Updated CLI binary for screenpipe search",
        ))

    def test_reordered_words_is_paraphrase(self):
        self.assertTrue(is_heading_paraphrase(
            "Updated screenpipe CLI search binary",
            "Updated CLI binary for screenpipe search",
        ))

    def test_subset_of_heading_is_paraphrase(self):
        """Bullet using fewer words than the heading but all from it."""
        self.assertTrue(is_heading_paraphrase(
            "Updated CLI binary",
            "Updated CLI binary for screenpipe search",
        ))

    def test_stopwords_do_not_inflate_overlap(self):
        """A bullet that only shares articles/prepositions with the
        heading must NOT be flagged, because the content words are
        actually different."""
        self.assertFalse(is_heading_paraphrase(
            "For the team meeting",
            "For the release notes",
        ))

    # ── True negatives: real bullets that share a word ──────────────

    def test_unrelated_bullet_with_one_shared_word(self):
        self.assertFalse(is_heading_paraphrase(
            "They confirmed search and tail work from terminal",
            "Updated CLI binary for screenpipe search",
        ))

    def test_bullet_with_specific_extra_content_is_kept(self):
        """When the bullet adds substantial new content beyond the
        heading, it should NOT be flagged as a paraphrase. Heading
        has 3 content tokens, bullet has 5 — half of bullet is new."""
        # heading content: stripe, webhook, setup (3)
        # bullet content:  stripe, webhook, setup, details, configured (5)
        # overlap = 3, ratio = 3/5 = 0.6 < 0.7 → not flagged
        self.assertFalse(is_heading_paraphrase(
            "Stripe webhook setup details now configured",
            "Stripe webhook setup",
        ))

    def test_completely_different_topic_is_kept(self):
        self.assertFalse(is_heading_paraphrase(
            "The user reviewed the new search commands",
            "Browsed GitHub repo about vector quantization",
        ))

    # ── Edge cases: empty / degenerate input ────────────────────────

    def test_empty_bullet_returns_false(self):
        self.assertFalse(is_heading_paraphrase("", "Heading"))

    def test_empty_heading_returns_false(self):
        self.assertFalse(is_heading_paraphrase("Bullet", ""))

    def test_both_empty_returns_false(self):
        self.assertFalse(is_heading_paraphrase("", ""))

    def test_bullet_with_only_stopwords_returns_false(self):
        """No content tokens to compare against — return False so a
        nonsense bullet does not get flagged as a heading echo."""
        self.assertFalse(is_heading_paraphrase("The a an or", "Real heading text"))

    # ── Threshold knob ──────────────────────────────────────────────

    def test_custom_threshold_can_be_stricter(self):
        """At threshold 0.9, a paraphrase with 75% overlap should NOT
        be flagged, allowing callers to dial sensitivity."""
        # heading: updated, cli, binary, screenpipe, search (5 content tokens)
        # bullet:  updated, cli, binary, screenpipe, search, functionality (6)
        # overlap = 5, ratio = 5/6 ≈ 0.83
        self.assertTrue(
            is_heading_paraphrase(
                "Updated CLI binary for screenpipe search functionality",
                "Updated CLI binary for screenpipe search",
                threshold=0.8,
            )
        )
        self.assertFalse(
            is_heading_paraphrase(
                "Updated CLI binary for screenpipe search functionality",
                "Updated CLI binary for screenpipe search",
                threshold=0.9,
            )
        )


if __name__ == "__main__":
    unittest.main()
