"""
Tests for the assistant chat branch of `_web_content_subject` (ChatGPT,
Claude, Perplexity, Gemini).

Regression target: `_ai_topic` used to carry a ladder of topic strings
hardcoded to the developer's own past sessions, so unrelated chats were
mislabeled. A chat that merely mentioned "first name" became "Asked Claude
about string transformations"; one mentioning an image and the word solve
became "Asked Claude about image problem solving". The fix drops the
overfit literals and derives the topic from the actual content, while real
content (e.g. a genuine prompt injection discussion) still surfaces.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.summaries import _web_content_subject


def _chat(body: str, title: str = "Claude") -> str:
    return f"{title} claude.com\nSite: claude.com\n{body}"


class TestAssistantTopicNoOverfit(unittest.TestCase):

    def test_first_name_chat_is_not_string_transformations(self):
        out = _web_content_subject(
            _chat("Help me plan a birthday party and collect each guest first name"),
            "Plan my party", "Used",
        )
        self.assertTrue(out.startswith("Asked Claude about"), out)
        self.assertNotIn("string transformation", out.lower())
        self.assertTrue("party" in out.lower() or "birthday" in out.lower(), out)

    def test_image_and_solve_chat_is_not_image_problem_solving(self):
        out = _web_content_subject(
            _chat("I want to solve the puzzle of why this image of my home feels nostalgic"),
            "Childhood memory", "Used",
        )
        self.assertNotIn("image problem solving", out.lower())

    def test_genuine_prompt_injection_topic_still_surfaces(self):
        out = _web_content_subject(
            _chat("Explain prompt injection attacks against LLM agents and defenses"),
            "Security", "Used",
        )
        self.assertIn("prompt injection", out.lower())

    def test_real_topic_words_are_content_derived(self):
        out = _web_content_subject(
            _chat("Explain how the Rust borrow checker prevents data races"),
            "Rust borrow checker", "Used",
        )
        self.assertTrue("rust" in out.lower() or "borrow" in out.lower(), out)


if __name__ == "__main__":
    unittest.main()
