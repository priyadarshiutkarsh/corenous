"""Tests for the embedding context envelope."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.embed_context import build_embed_text


class TestBuildEmbedText(unittest.TestCase):
    def test_prepends_useful_context(self):
        out = build_embed_text("the body text", ["Scaling Laws - arXiv", "Safari", "May 7 2023"])
        self.assertEqual(out, "Scaling Laws - arXiv\nSafari\nMay 7 2023\nthe body text")

    def test_drops_empty_and_noise(self):
        out = build_embed_text("body", ["", "Untitled", "https://example.com/x", "Notes"])
        self.assertEqual(out, "Notes\nbody")

    def test_no_useful_context_returns_body_unchanged(self):
        self.assertEqual(build_embed_text("body", ["", "New Tab", "about:blank"]), "body")

    def test_dedups_repeated_context(self):
        out = build_embed_text("body", ["Safari", "safari", "Safari"])
        self.assertEqual(out, "Safari\nbody")

    def test_empty_body_with_context(self):
        self.assertEqual(build_embed_text("", ["Notes"]), "Notes")


if __name__ == "__main__":
    unittest.main()
