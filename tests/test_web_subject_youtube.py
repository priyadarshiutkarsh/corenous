"""
Tests for the YouTube branch of `_web_content_subject` — feed/home pages
must NOT fabricate a video title from the first OCR line (which is the
site logo or an ad thumbnail), while genuine watch pages still resolve to
the actual video subject.

Regression target: the home feed produced "Watched tube youtube" because
`body_lines[0]` was the site logo, and `is_watch` defaulted True on any
non-search title.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.summaries import _web_content_subject


class TestYouTubeSubject(unittest.TestCase):

    def test_home_feed_does_not_fabricate_video_title(self):
        """The home feed tab title is just 'YouTube'. The first OCR line is
        the logo, so we must return a neutral label, never 'Watched tube
        youtube'."""
        text = (
            "YouTube youtube.com\n=\nYouTube\nHome\nShorts\nSubscriptions\n"
            "Gutka Ads Deserves A Belt Treatment\nBig Laksh"
        )
        out = _web_content_subject(text, "YouTube", "Watched")
        self.assertEqual(out, "Browsed YouTube")
        self.assertNotIn("tube youtube", out.lower())

    def test_home_feed_with_unread_badge(self):
        """A leading '(3) ' notification badge must not defeat feed
        detection."""
        text = "YouTube youtube.com\nYouTube\nHome\nShorts"
        out = _web_content_subject(text, "(3) YouTube", "Watched")
        self.assertEqual(out, "Browsed YouTube")

    def test_section_pages_are_neutral(self):
        for title in ("Subscriptions", "Shorts", "Trending", "Watch later", "History"):
            text = f"{title} youtube.com\n{title}\nsome thumbnail row"
            out = _web_content_subject(text, title, "Watched")
            self.assertEqual(out, "Browsed YouTube", f"title={title!r} -> {out!r}")

    def test_genuine_watch_page_still_resolves_video(self):
        """A real watch page: the body's first line is the video title, and
        the result should name the video — no regression from the feed
        guard."""
        text = (
            "Backpropagation explained youtube.com\n"
            "Backpropagation explained clearly with examples\n"
            "Channel: Stanford Online"
        )
        out = _web_content_subject(text, "Backpropagation explained - YouTube", "Watched")
        self.assertTrue(out.lower().startswith("watched"), out)
        self.assertIn("backprop", out.lower())

    def test_logo_only_body_does_not_emit_tube_youtube(self):
        """Defense in depth: even if a non-feed title slips through but the
        first OCR line is just the site logo, we must not emit the junk
        'tube youtube' phrasing."""
        text = "Something youtube.com\nYouTube\n"
        out = _web_content_subject(text, "Something Odd", "Watched")
        self.assertNotIn("tube youtube", out.lower())


if __name__ == "__main__":
    unittest.main()
