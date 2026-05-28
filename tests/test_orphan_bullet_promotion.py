"""
Tests for the orphan-bullet promotion fix in ai_memory_bullets.

Llama 3.2 3B sometimes forgets the leading "• " glyph on continuation
lines. The previous bullet collector silently dropped any line that
didn't start with • or "- ", so real content was lost. The fix
promotes orphan lines that read like complete sentences (capital
start, terminal punctuation, 4+ words) while still discarding short
fragments and chrome.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ai.summarizer import ai_memory_bullets


_PROSE_BODY = (
    "The user reviewed the new CLI commands they had recently added. "
    "They confirmed that several subcommands worked from the terminal. "
    "They then discussed whether to commit each improvement separately. "
    "The session concluded with a clear plan for shipping the changes."
)


def _run(raw_model_output: str, body_text: str = _PROSE_BODY) -> list[str]:
    """Invoke ai_memory_bullets with a stubbed model response.

    The default body is multi sentence prose so the low signal pre check
    in ai_memory_bullets does not short circuit before reaching the
    mocked AI call. Tests that want to exercise the pre check itself
    pass their own deliberately sparse body.
    """
    with patch("src.ai.summarizer.infer", return_value=raw_model_output):
        out = ai_memory_bullets(body_text, heading="", app_name="Test", window_title="W", activity="A")
    return [ln for ln in out.splitlines() if ln.strip()]


class TestOrphanBulletPromotion(unittest.TestCase):

    def test_orphan_complete_sentence_is_captured(self):
        """A line that the model produced WITHOUT the • glyph but that is
        a complete sentence (capital start, period end, 4+ words) must
        end up as its own bullet, not be silently dropped."""
        raw = (
            "• First proper bullet here.\n"
            "The user reviewed the new CLI commands they had added.\n"
            "• Third proper bullet."
        )
        bullets = _run(raw)
        joined = " ".join(bullets)
        self.assertIn("The user reviewed", joined)
        # All three sentences survive as bullets.
        self.assertEqual(len(bullets), 3)

    def test_short_orphan_fragment_is_still_dropped(self):
        """Single capitalized words like 'Gurugram' or sidebar items
        like 'Corenous repository review' must NOT be promoted —
        promotion is gated on terminal punctuation."""
        raw = (
            "• Proper bullet one.\n"
            "Gurugram\n"
            "Corenous repository review\n"
            "Updated CLI binary for screenpipe search\n"
            "• Proper bullet two."
        )
        bullets = _run(raw)
        joined = " ".join(bullets)
        self.assertNotIn("Gurugram", joined)
        self.assertNotIn("Corenous repository review", joined)
        self.assertNotIn("Updated CLI binary for screenpipe search", joined)
        self.assertEqual(len(bullets), 2)

    def test_lowercase_orphan_is_dropped_even_with_period(self):
        """Sentence promotion requires a capitalized first letter so
        we do not accidentally pick up trailing prose snippets that
        the prompt rules already disallow."""
        raw = (
            "• Proper bullet here.\n"
            "• Another proper bullet for the count.\n"
            "lowercase orphan that ends with a period."
        )
        bullets = _run(raw)
        # 2 proper bullets survive, the lowercase orphan is dropped.
        self.assertEqual(len(bullets), 2)

    def test_short_orphan_with_period_is_dropped(self):
        """3 word sentences are too short to be useful as bullets and
        also too easily false positive on chrome like 'Last updated today.'"""
        raw = (
            "• Proper bullet here.\n"
            "• Another proper bullet for the count.\n"
            "Three short words."
        )
        bullets = _run(raw)
        # 2 proper bullets survive, the 3 word orphan is dropped.
        self.assertEqual(len(bullets), 2)

    def test_realistic_failure_case_from_bug_report(self):
        """Replays the exact pattern observed in the live Llama test:
        one well-bulleted output, three chrome-shaped orphans the model
        emitted between bullets, and one real continuation sentence."""
        raw = (
            "• Gurugram\n"
            "Corenous repository review\n"
            "Updated CLI binary for screenpipe search\n"
            "The user reviewed the new screenpipe style CLI commands they added to corenous.\n"
            "• They confirmed search recent and tail all work from the terminal.\n"
            "• They discussed whether to commit each improvement as a separate logical commit."
        )
        bullets = _run(raw)
        joined = "\n".join(bullets)
        # The real sentence the model orphaned must survive.
        self.assertIn("The user reviewed", joined)
        # The chrome-shaped orphans must not.
        self.assertNotIn("Corenous repository review", joined)
        self.assertNotIn("Updated CLI binary for screenpipe search", joined)


if __name__ == "__main__":
    unittest.main()
