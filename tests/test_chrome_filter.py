"""
Tests for strip_ui_chrome — pre filter that drops OS / app chrome lines
from OCR text before the local LLM sees it.

Without this filter, Llama 3.2 3B treats sidebar nav items, weather
widgets, and "Relaunch to update v1.9255.0" badges as memory content
and writes summaries citing them. See the bug screenshot in commit
892e41d for the original symptom.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.summaries import strip_ui_chrome


class TestStripUIChrome(unittest.TestCase):

    # ── version + update banners ─────────────────────────────────────

    def test_drops_relaunch_to_update_banner(self):
        text = "Real content here\nRelaunch to update v1.9255.0\nMore content"
        out = strip_ui_chrome(text)
        self.assertNotIn("Relaunch", out)
        self.assertNotIn("1.9255", out)
        self.assertIn("Real content here", out)
        self.assertIn("More content", out)

    def test_drops_lone_version_label(self):
        out = strip_ui_chrome("Some text\nv1.2.3\nMore text")
        self.assertNotIn("v1.2.3", out)
        self.assertIn("Some text", out)

    def test_keeps_version_inside_a_sentence(self):
        """Only WHOLE lines that are nothing but a version are stripped.
        'We shipped v0.0.1 today' is real content and must stay."""
        text = "We shipped v0.0.1 today"
        self.assertEqual(strip_ui_chrome(text), text)

    # ── status bar atoms ─────────────────────────────────────────────

    def test_drops_lone_battery_percentage(self):
        out = strip_ui_chrome("Content\n85%\nMore")
        self.assertNotIn("85%", out)

    def test_keeps_percentage_inside_sentence(self):
        text = "Hit rate climbed to 85% after the rebuild"
        self.assertEqual(strip_ui_chrome(text), text)

    def test_drops_lone_time(self):
        out = strip_ui_chrome("Content\n12:00 am\nMore")
        self.assertNotIn("12:00 am", out)

    def test_drops_lone_temperature(self):
        out = strip_ui_chrome("Content\n102°\nMore")
        self.assertNotIn("102°", out)

    # ── weather widget ───────────────────────────────────────────────

    def test_drops_weather_label(self):
        out = strip_ui_chrome("Content\nSunny\nMore")
        self.assertNotIn("Sunny", out)

    def test_drops_high_low_temp_line(self):
        out = strip_ui_chrome("Content\nH:111° L:82°\nMore")
        self.assertNotIn("H:111", out)

    # ── weekday + date + time ────────────────────────────────────────

    def test_drops_status_bar_date(self):
        out = strip_ui_chrome("Content\nTue 27 May 12:00 am\nMore")
        self.assertNotIn("Tue 27 May", out)

    def test_keeps_date_inside_a_sentence(self):
        text = "The meeting on Tue 27 May was rescheduled"
        self.assertEqual(strip_ui_chrome(text), text)

    # ── sidebar nav items ────────────────────────────────────────────

    def test_drops_one_word_nav_items(self):
        text = "Routines\nCustomize\nMore\nRecents\nActual page body text here"
        out = strip_ui_chrome(text)
        # All four nav items gone, only the body survives.
        self.assertNotIn("Routines", out)
        self.assertNotIn("Customize", out)
        self.assertNotIn("Recents", out)
        self.assertIn("Actual page body text here", out)

    def test_drops_new_session(self):
        out = strip_ui_chrome("New session\nReal content")
        self.assertNotIn("New session", out)

    def test_keeps_long_lines_even_if_starting_with_nav_word(self):
        """A real content line that happens to start with a nav word
        ('Settings for the production environment') must NOT be dropped."""
        text = "Settings for the production environment were rotated"
        self.assertEqual(strip_ui_chrome(text), text)

    # ── safety: empty + plain content ────────────────────────────────

    def test_empty_input_returns_empty(self):
        self.assertEqual(strip_ui_chrome(""), "")
        self.assertIsNone(strip_ui_chrome(None))  # type: ignore[arg-type]

    def test_clean_content_passes_through_unchanged(self):
        text = "The user opened a GitHub PR.\nThe diff added 388 lines."
        self.assertEqual(strip_ui_chrome(text), text)

    def test_collapses_runs_of_blank_lines_left_by_chrome(self):
        """When chrome is stripped between real content lines, the
        result should not leave a giant gap of blank lines."""
        text = "Real line one\n85%\n12:00 am\nSunny\nReal line two"
        out = strip_ui_chrome(text)
        # No 3+ consecutive newlines.
        self.assertNotIn("\n\n\n", out)
        self.assertIn("Real line one", out)
        self.assertIn("Real line two", out)

    # ── integration: actual screenshot chrome ────────────────────────

    def test_screenshot_ocr_blob_is_cleaned(self):
        """The literal mess of chrome that the OCR captured around the
        Claude app in the bug report screenshot must be stripped to
        leave only the conversation content."""
        ocr_blob = (
            "Tue 27 May 12:00 am\n"
            "82%\n"
            "Gurugram\n"
            "102°\n"
            "Sunny\n"
            "H:111° L:82°\n"
            "New session\n"
            "Routines\n"
            "Customize\n"
            "More\n"
            "Recents\n"
            "Corenous repository review\n"
            "Relaunch to update v1.9255.0\n"
            "shouldn't this be in bullets\n"
            "is it hallucinating or now fine?"
        )
        out = strip_ui_chrome(ocr_blob)
        # All chrome stripped.
        self.assertNotIn("12:00 am", out)
        self.assertNotIn("82%", out)
        self.assertNotIn("Sunny", out)
        self.assertNotIn("H:111", out)
        self.assertNotIn("New session", out)
        self.assertNotIn("Routines", out)
        self.assertNotIn("Customize", out)
        self.assertNotIn("Recents", out)
        self.assertNotIn("v1.9255", out)
        self.assertNotIn("Relaunch to update", out)
        # Real content kept.
        self.assertIn("Corenous repository review", out)  # title still passes (looks like content)
        self.assertIn("shouldn't this be in bullets", out)
        self.assertIn("is it hallucinating or now fine?", out)


if __name__ == "__main__":
    unittest.main()
