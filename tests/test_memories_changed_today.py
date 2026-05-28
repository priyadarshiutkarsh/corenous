"""
Tests for `corenous-ai memories changed-today` — set difference of
today's (app, canonical_window_signature) pairs vs the prior N days.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.cli.main import cli


def _row(
    id: int,
    *,
    app_name: str = "Chrome",
    window_title: str = "Some page",
    heading: str = "",
    created_at: float = 1_700_000_000.0,
) -> dict:
    return {
        "id": id,
        "app_name": app_name,
        "window_title": window_title,
        "heading": heading,
        "created_at": created_at,
        "source": "browser",
        "activity": "Browsed",
        "summary": "",
        "narrative": "",
        "text_snippet": "",
        "tags": "",
    }


def _invoke(args: list[str], *, today_rows: list[dict], baseline_rows: list[dict]):
    app = MagicMock()
    # Two calls: first for today's window, then for the baseline window.
    app.store.get_memories_in_range.side_effect = [today_rows, baseline_rows]
    runner = CliRunner()
    with patch("src.cli.main.AppContext.load", return_value=app):
        result = runner.invoke(cli, args, catch_exceptions=False)
    return result, app


class TestMemoriesChangedToday(unittest.TestCase):

    def test_drops_sessions_seen_in_baseline(self):
        """A window that appeared in the last N days must NOT appear in
        the 'new today' list, even if it shows up many times today."""
        today = [
            _row(1, window_title="Gmail Inbox", created_at=1.0),
            _row(2, window_title="Gmail Inbox", created_at=2.0),
            _row(3, window_title="NEW thing", created_at=3.0),
        ]
        baseline = [
            _row(100, window_title="Gmail Inbox", created_at=-86400.0),
        ]
        result, _ = _invoke(
            ["memories", "changed-today", "--json"],
            today_rows=today, baseline_rows=baseline,
        )
        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.output)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["title"], "NEW thing")

    def test_collapses_notification_badge_variants(self):
        """Today's '(3) Inbox' and baseline's 'Inbox' should be
        considered the same session by canonical signature, so this
        Inbox would NOT appear as 'new today'."""
        today = [
            _row(1, window_title="(3) Inbox - Gmail", created_at=1.0),
        ]
        baseline = [
            _row(100, window_title="Inbox - Gmail", created_at=-86400.0),
        ]
        result, _ = _invoke(
            ["memories", "changed-today", "--json"],
            today_rows=today, baseline_rows=baseline,
        )
        payload = json.loads(result.output)
        self.assertEqual(payload, [])

    def test_distinct_app_with_same_title_counts_as_new(self):
        """Same window title but in a different app is a different
        session and should appear as new."""
        today = [
            _row(1, app_name="Safari", window_title="Settings"),
        ]
        baseline = [
            _row(100, app_name="Chrome", window_title="Settings"),
        ]
        result, _ = _invoke(
            ["memories", "changed-today", "--json"],
            today_rows=today, baseline_rows=baseline,
        )
        payload = json.loads(result.output)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["app"], "Safari")

    def test_sorted_by_today_capture_count_desc(self):
        today = [
            _row(1, window_title="A"),
            _row(2, window_title="B"),
            _row(3, window_title="B"),
            _row(4, window_title="B"),
            _row(5, window_title="C"),
            _row(6, window_title="C"),
        ]
        baseline: list[dict] = []
        result, _ = _invoke(
            ["memories", "changed-today", "--json"],
            today_rows=today, baseline_rows=baseline,
        )
        payload = json.loads(result.output)
        self.assertEqual([s["title"] for s in payload], ["B", "C", "A"])

    def test_empty_today_short_circuits(self):
        result, _ = _invoke(
            ["memories", "changed-today", "--json"],
            today_rows=[], baseline_rows=[_row(1)],
        )
        self.assertEqual(result.output.strip(), "[]")

    def test_no_new_sessions_friendly_message(self):
        """When everything today was also in baseline, the human output
        explains that explicitly instead of just printing nothing."""
        today = [_row(1, window_title="X")]
        baseline = [_row(100, window_title="X")]
        result, _ = _invoke(
            ["memories", "changed-today"],
            today_rows=today, baseline_rows=baseline,
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("nothing new", result.output.lower())

    def test_baseline_days_flag_changes_query_window(self):
        """The baseline-days flag scales the comparison window. Verified
        via the mocked side_effect order: first call is today, second
        is baseline. The second call's start_ts should equal
        today_start - baseline_days * 86400."""
        result, app = _invoke(
            ["memories", "changed-today", "--baseline-days", "30", "--json"],
            today_rows=[_row(1)], baseline_rows=[],
        )
        self.assertEqual(result.exit_code, 0)
        calls = app.store.get_memories_in_range.call_args_list
        today_start = calls[0].args[0]
        baseline_start = calls[1].args[0]
        baseline_end = calls[1].args[1]
        self.assertAlmostEqual(baseline_end, today_start, places=0)
        self.assertAlmostEqual(today_start - baseline_start, 30 * 86400.0, places=0)


if __name__ == "__main__":
    unittest.main()
