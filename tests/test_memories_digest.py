"""
Tests for `corenous-ai memories digest` — phase 1 of the daily digest
feature. Pulls memories for one calendar day, runs ai_daily_digest,
prints the result.
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.cli.main import cli, _parse_day_arg


# ── _parse_day_arg ───────────────────────────────────────────────────────

class TestParseDayArg(unittest.TestCase):

    def test_today_resolves_to_local_midnight_boundary(self):
        start, end, label = _parse_day_arg("today")
        today_midnight = datetime.combine(date.today(), datetime.min.time())
        self.assertEqual(start, today_midnight.timestamp())
        self.assertEqual(end - start, 86400.0)
        self.assertEqual(label, "Today")

    def test_yesterday_resolves_one_day_earlier(self):
        start, end, label = _parse_day_arg("yesterday")
        expected_start = datetime.combine(
            date.today() - timedelta(days=1), datetime.min.time()
        )
        self.assertEqual(start, expected_start.timestamp())
        self.assertEqual(label, "Yesterday")

    def test_iso_date_resolves_with_weekday_label(self):
        start, _end, label = _parse_day_arg("2026-05-27")
        expected_start = datetime.combine(
            date(2026, 5, 27), datetime.min.time()
        )
        self.assertEqual(start, expected_start.timestamp())
        # Label format: "Wednesday, May 27"
        self.assertIn("May 27", label)

    def test_case_insensitive_today_and_yesterday(self):
        s1, _, l1 = _parse_day_arg("TODAY")
        s2, _, l2 = _parse_day_arg("today")
        self.assertEqual(s1, s2)
        self.assertEqual(l1, "Today")

    def test_invalid_string_raises_bad_parameter(self):
        import click
        with self.assertRaises(click.BadParameter):
            _parse_day_arg("not-a-date")
        with self.assertRaises(click.BadParameter):
            _parse_day_arg("")

    def test_invalid_iso_format_raises_bad_parameter(self):
        import click
        with self.assertRaises(click.BadParameter):
            _parse_day_arg("2026/05/27")  # wrong separator
        with self.assertRaises(click.BadParameter):
            _parse_day_arg("05-27-2026")  # wrong order


# ── memories digest CLI integration ──────────────────────────────────────

def _row(id: int = 1, ts_offset: float = 0.0) -> dict:
    return {
        "id": id,
        "app_name": "Chrome",
        "window_title": "Some page",
        "activity": "Browsed",
        "heading": "Read something",
        "summary": "topic",
        "text_snippet": "the user looked at a page about widgets",
        "created_at": time.time() + ts_offset,
        "source": "browser",
    }


def _invoke(args: list[str], rows: list[dict], digest_output: str = "• A useful digest line."):
    app = MagicMock()
    store = MagicMock()
    store.get_memories_in_range.return_value = rows
    app.store = store
    runner = CliRunner()
    with patch("src.cli.main.AppContext.load", return_value=app), \
         patch("src.ai.llm.load_model_sync", return_value=True), \
         patch("src.ai.summarizer.ai_daily_digest", return_value=digest_output) as mock_ai:
        result = runner.invoke(cli, args, catch_exceptions=False)
    return result, app, mock_ai


class TestMemoriesDigestCommand(unittest.TestCase):

    def test_today_default_prints_digest(self):
        result, app, mock_ai = _invoke(
            ["memories", "digest"], [_row(1), _row(2)],
            digest_output="A day of focused work.\n• Reviewed two pages."
        )
        self.assertEqual(result.exit_code, 0)
        mock_ai.assert_called_once()
        self.assertIn("Reviewed two pages", result.output)

    def test_passes_correct_day_label_to_ai(self):
        result, app, mock_ai = _invoke(
            ["memories", "digest", "--day", "yesterday"], [_row()],
        )
        self.assertEqual(result.exit_code, 0)
        kwargs = mock_ai.call_args.kwargs
        self.assertEqual(kwargs.get("day_label"), "Yesterday")

    def test_iso_date_label_includes_weekday(self):
        result, app, mock_ai = _invoke(
            ["memories", "digest", "--day", "2026-05-27"], [_row()],
        )
        self.assertEqual(result.exit_code, 0)
        kwargs = mock_ai.call_args.kwargs
        self.assertIn("May 27", kwargs.get("day_label"))

    def test_no_memories_prints_friendly_message(self):
        """An empty day must not call the AI or raise — just say so."""
        result, app, mock_ai = _invoke(
            ["memories", "digest", "--day", "yesterday"], rows=[]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("No memories", result.output)
        mock_ai.assert_not_called()

    def test_empty_ai_response_surfaces_friendly_error(self):
        """A degenerate AI response must not silently print blank."""
        result, app, mock_ai = _invoke(
            ["memories", "digest"], [_row()], digest_output=""
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("empty", result.output.lower())

    def test_invalid_day_arg_exits_nonzero(self):
        app = MagicMock()
        app.store = MagicMock()
        runner = CliRunner()
        with patch("src.cli.main.AppContext.load", return_value=app):
            result = runner.invoke(cli, ["memories", "digest", "--day", "garbage"],
                                   catch_exceptions=False)
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("today", result.output.lower())  # error message mentions valid forms


if __name__ == "__main__":
    unittest.main()
