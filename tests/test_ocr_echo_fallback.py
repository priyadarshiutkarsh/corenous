"""
Tests for the low-signal echo fallback in ai_memory_bullets.

When Llama 3.2 3B is fed mostly UI nav + marketing copy + word fragments
(an app store listing, a social feed home, a category page), it produces
bullets that just restate the OCR rather than summarize the user's
activity. _is_ocr_echo detects this; _contextual_summary_fallback
produces a one bullet summary from metadata that works for any site.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ai.summarizer import (
    _is_ocr_echo,
    _is_low_signal_input,
    _count_full_sentences,
    _fragment_line_ratio,
    _contextual_summary_fallback,
    ai_memory_bullets,
)


# ── _fragment_line_ratio ─────────────────────────────────────────────────

class TestFragmentLineRatio(unittest.TestCase):

    def test_all_short_lines_return_high_ratio(self):
        text = "Games\nApps\nMovies TV\nBooks\nKids"
        self.assertGreater(_fragment_line_ratio(text), 0.9)

    def test_all_prose_lines_return_low_ratio(self):
        text = (
            "The article discussed runtime internals at length.\n"
            "The author argued that pin projection is leaky.\n"
            "Several follow up posts were referenced."
        )
        self.assertLess(_fragment_line_ratio(text), 0.1)

    def test_mixed_content_returns_proportional_ratio(self):
        text = "Short\nAnother short\nA much longer line of real content here"
        # 2 short out of 3 = 0.66
        ratio = _fragment_line_ratio(text)
        self.assertGreater(ratio, 0.5)
        self.assertLess(ratio, 0.8)

    def test_empty_text_returns_one(self):
        self.assertEqual(_fragment_line_ratio(""), 1.0)
        self.assertEqual(_fragment_line_ratio("   \n  \n"), 1.0)


# ── _is_low_signal_input ─────────────────────────────────────────────────

class TestIsLowSignalInput(unittest.TestCase):

    def test_short_input_is_low_signal(self):
        self.assertTrue(_is_low_signal_input("login"))
        self.assertTrue(_is_low_signal_input("very short bit"))

    def test_app_store_listing_is_low_signal(self):
        """The actual VPN page OCR has lots of word fragments but only
        one or two real sentences. Must be flagged as low signal."""
        vpn_ocr = (
            "Android Apps by Free VPN Planet on Google Play play.google.com\n"
            "Google Play\nGames\nApps\nMovies & TV\nBooks\nKids\n"
            "Free VPN Planet\nPlanet Free VPN Super Proxy\n"
            "Contains ads In-app purchases\n"
            "Planet Free VPN - The best Fast Unlimited and Secure VPN Without Logs\n"
            "4.5\nVPN\n1.95M reviews\n10M+\nDownloads\nInstall\n"
            "ESRB\nEveryone\nMore by Free VPN Planet\n"
            "100% free VPN service with no traffic or time limit and it's FAST!\n"
            "Just install and connect"
        )
        self.assertTrue(_is_low_signal_input(vpn_ocr))

    def test_article_text_is_not_low_signal(self):
        """A normal article with 3+ full sentences must pass through."""
        article = (
            "The article discussed tokio runtime internals and how the "
            "executor polls futures. The author argued that pin projection "
            "is a leaky abstraction. A worked example showed how to compose "
            "channel receivers across many tasks. The piece ended with a "
            "benchmark of work stealing schedulers."
        )
        self.assertFalse(_is_low_signal_input(article))

    def test_decimals_do_not_inflate_sentence_count(self):
        """'Version 3.14 of the library' has a dot but is not a sentence
        boundary. Must not trick the count into reporting low signal as
        high signal."""
        text = "Version 3.14 of the library was 1.95 MB and 4.5 stars rated"
        # 0 real terminal periods in this string
        self.assertEqual(_count_full_sentences(text), 0)

    def test_count_full_sentences_handles_terminal_punct(self):
        text = "First sentence. Second one! Third? Done."
        self.assertEqual(_count_full_sentences(text), 4)

    def test_marketing_copy_with_taglines_is_low_signal(self):
        """Pages dominated by short marketing taglines (each ending with !
        or .) escape the sentence count check because they have many
        punctuated lines. The fragment ratio check catches them: most
        lines are still 3 words or fewer."""
        ocr = (
            "Free VPN Planet\nGames\nApps\nMovies & TV\nBooks\nKids\n"
            "VPN\nInstall\nEveryone\n100% free VPN service with no traffic.\n"
            "NO LIMITS\nNO LOGS\nFAST\n10M+\nDownloads\n4.5\nESRB\nPLON\n"
            "Just install and connect, no registration needed.\n"
            "OVER 10 MILLION USERS WORLDWIDE"
        )
        self.assertTrue(_is_low_signal_input(ocr))

    def test_one_long_paragraph_is_not_low_signal(self):
        """A single line of real prose (one paragraph) must not trip the
        fragment ratio check, even though it is 1 line out of 1."""
        text = (
            "The article walked through several optimization techniques for "
            "async runtimes, comparing work stealing schedulers with single "
            "threaded executors under bursty workloads. The author concluded "
            "that the right choice depends on the latency profile of the "
            "underlying tasks."
        )
        self.assertFalse(_is_low_signal_input(text))


# ── _is_ocr_echo ─────────────────────────────────────────────────────────

class TestIsOcrEcho(unittest.TestCase):

    def test_echo_of_input_is_detected(self):
        """When the bullets contain the same words as input, return True."""
        input_text = (
            "Google Play Games Apps Movies TV Books Kids Free VPN Planet "
            "Planet Free VPN Super Proxy Contains ads In-app purchases "
            "Install ESRB Everyone More by Free VPN Planet 100% free VPN "
            "service with no traffic or time limit and it's FAST Just install "
            "and connect no registration or personal data required OVER 10 "
            "MILLION USERS WORLDWIDE"
        )
        # Bullets that just echo the input
        echo_bullets = (
            "• Google Play\n• Games Apps Movies TV Books Kids\n"
            "• Free VPN Planet Planet Free VPN Super Proxy\n"
            "• Contains ads In-app purchases Install\n"
            "• 100% free VPN service no traffic"
        )
        self.assertTrue(_is_ocr_echo(echo_bullets, input_text))

    def test_real_summary_is_not_detected_as_echo(self):
        """Bullets that genuinely summarize (use meta verbs, add framing)
        share fewer words with input and must NOT be flagged."""
        input_text = (
            "Google Play Games Apps Movies TV Books Kids Free VPN Planet "
            "Contains ads In-app purchases 4.5 stars 10M downloads Install "
            "ESRB Everyone 100% free VPN service no traffic time limit and "
            "it's FAST Just install and connect no registration or personal "
            "data required"
        )
        real_summary = (
            "• Browsed an Android VPN listing on the Play Store.\n"
            "• The featured app was rated highly with millions of users.\n"
            "• Marketing emphasized privacy claims and absence of logs."
        )
        self.assertFalse(_is_ocr_echo(real_summary, input_text))

    def test_tiny_input_is_not_flagged(self):
        """Very short captures naturally overlap with any output and
        would false positive. The function skips the check when input
        has fewer than _ECHO_MIN_INPUT_TOKENS content tokens."""
        small_input = "Login page username password"
        same_bullets = "• Login page username password"
        self.assertFalse(_is_ocr_echo(same_bullets, small_input))

    def test_empty_inputs_return_false(self):
        self.assertFalse(_is_ocr_echo("", "real input here"))
        self.assertFalse(_is_ocr_echo("• something", ""))
        self.assertFalse(_is_ocr_echo("", ""))


# ── _contextual_summary_fallback ─────────────────────────────────────────

class TestContextualSummaryFallback(unittest.TestCase):

    def test_uses_activity_plus_window_title(self):
        out = _contextual_summary_fallback(
            heading="",
            window_title="Android Apps by Free VPN Planet on Google Play",
            app_name="Google Chrome",
            activity="Browsed Google.com",
        )
        self.assertIn("Browsed Google.com", out)
        self.assertIn("Android Apps by Free VPN Planet on Google Play", out)
        self.assertTrue(out.startswith("• "))

    def test_falls_back_to_window_title_in_app(self):
        out = _contextual_summary_fallback(
            heading="",
            window_title="Settings preferences",
            app_name="System Settings",
            activity="",
        )
        self.assertIn("Settings preferences", out)
        self.assertIn("System Settings", out)

    def test_falls_back_to_heading_when_no_other_metadata(self):
        out = _contextual_summary_fallback(
            heading="Reviewed pull request about token overlap",
            window_title="",
            app_name="",
            activity="",
        )
        self.assertIn("Reviewed pull request about token overlap", out)

    def test_does_not_duplicate_activity_and_window_when_identical(self):
        """If activity and window_title carry the same string, do not
        produce a bullet like 'X, viewing X.' — use the simpler form."""
        out = _contextual_summary_fallback(
            heading="",
            window_title="Some page title",
            app_name="Safari",
            activity="Some page title",
        )
        # Should not have ", viewing" appended when the two fields match
        self.assertNotIn(", viewing", out)

    def test_empty_metadata_gives_honest_placeholder(self):
        out = _contextual_summary_fallback(
            heading="", window_title="", app_name="", activity=""
        )
        self.assertTrue(out.startswith("• "))
        self.assertIn("too thin", out.lower())

    def test_returned_string_is_a_single_bullet(self):
        out = _contextual_summary_fallback(
            heading="Heading",
            window_title="Window",
            app_name="App",
            activity="Browsed",
        )
        self.assertEqual(out.count("\n"), 0)
        self.assertTrue(out.startswith("• "))


# ── ai_memory_bullets integration ────────────────────────────────────────

class TestAiMemoryBulletsEchoIntegration(unittest.TestCase):

    def _run(self, raw_model_output: str, body_text: str, **meta) -> str:
        with patch("src.ai.summarizer.infer", return_value=raw_model_output):
            return ai_memory_bullets(body_text, **meta)

    def test_echo_output_is_replaced_with_contextual_summary(self):
        # Long body, the model echoes the body back as bullets
        body = (
            "Google Play Games Apps Movies TV Books Kids Free VPN Planet "
            "Planet Free VPN Super Proxy Contains ads In-app purchases "
            "Install Everyone More by Free VPN Planet 100 free VPN service "
            "with no traffic or time limit and it is FAST Just install and "
            "connect no registration or personal data required OVER MILLION "
            "USERS WORLDWIDE NO LIMITS NO LOGS"
        )
        echo = (
            "• Google Play Games Apps Movies TV Books Kids\n"
            "• Free VPN Planet Planet Free VPN Super Proxy\n"
            "• Contains ads In-app purchases Install\n"
            "• Free VPN service no traffic time limit"
        )
        out = self._run(
            echo, body,
            heading="",
            app_name="Google Chrome",
            window_title="Android Apps by Free VPN Planet on Google Play",
            activity="Browsed Google.com",
        )
        # Echo was replaced with a single contextual bullet
        self.assertEqual(out.count("\n"), 0)
        self.assertTrue(out.startswith("• "))
        self.assertIn("Android Apps by Free VPN Planet on Google Play", out)

    def test_real_summary_passes_through_unchanged(self):
        # Real prose input (not a bag of tokens) so the low signal pre check
        # does not short circuit before reaching the AI call we are testing.
        body = (
            "The article discussed tokio runtime internals and how the "
            "executor polls futures. The author argued that pin projection "
            "is a leaky abstraction. A worked example showed how to compose "
            "channel receivers across many tasks under bursty load. "
            "The piece ended with benchmarks of work stealing schedulers."
        )
        real = (
            "• Read about Rust async patterns focused on tokio runtime.\n"
            "• Notes covered the select macro and how to compose futures.\n"
            "• Discussion touched on borrow checker rules for async closures."
        )
        out = self._run(
            real, body,
            heading="",
            app_name="Google Chrome",
            window_title="Tokio docs",
            activity="Browsed tokio.rs",
        )
        # Real summary preserved, not replaced by contextual fallback
        self.assertIn("Read about Rust async", out)
        self.assertIn("select macro", out)


if __name__ == "__main__":
    unittest.main()
