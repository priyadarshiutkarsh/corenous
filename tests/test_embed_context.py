"""Tests for the embedding context envelope."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.embed_context import build_embed_text, capture_embed_text


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


class TestCaptureEmbedText(unittest.TestCase):
    """The production envelope the daemon and reindex apply before embedding."""

    TS = 1749600000.0  # a fixed instant; only the date words matter

    def test_full_envelope_order(self):
        out = capture_embed_text(
            "the body",
            window_title="Scaling Laws - arXiv",
            app_name="Safari",
            ts=self.TS,
            prev_heading="Reading about transformers",
        )
        lines = out.split("\n")
        self.assertEqual(lines[0], "Scaling Laws - arXiv")
        self.assertEqual(lines[1], "Safari")
        self.assertEqual(lines[3], "Reading about transformers")
        self.assertEqual(lines[4], "the body")
        # date line carries real calendar words
        import time as _t
        self.assertEqual(lines[2], _t.strftime("%A %d %B %Y", _t.localtime(self.TS)))

    def test_no_ts_means_no_date_line(self):
        out = capture_embed_text("body", window_title="Notes", app_name="", ts=None)
        self.assertEqual(out, "Notes\nbody")

    def test_noisy_window_title_dropped(self):
        out = capture_embed_text("body", window_title="https://x.test/page", app_name="Arc")
        self.assertEqual(out, "Arc\nbody")

    def test_bare_body_unchanged_without_context(self):
        self.assertEqual(capture_embed_text("just the body"), "just the body")


if __name__ == "__main__":
    unittest.main()
