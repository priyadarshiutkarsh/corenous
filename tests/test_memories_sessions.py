"""
Tests for `corenous-ai memories sessions` — heuristic ranking by where
the user spent the most time today. Pure SQL plus canonical window
signature grouping, no model dependency.
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


def _invoke(args: list[str], rows: list[dict]):
    app = MagicMock()
    app.store.get_memories_in_range.return_value = rows
    runner = CliRunner()
    with patch("src.cli.main.AppContext.load", return_value=app):
        result = runner.invoke(cli, args, catch_exceptions=False)
    return result, app


class TestMemoriesSessions(unittest.TestCase):

    def test_groups_by_canonical_signature_not_raw_title(self):
        """Captures with notification badge variations like '(3) Inbox'
        and '(5) Inbox' must collapse into one session."""
        rows = [
            _row(1, app_name="Chrome", window_title="(2) Inbox - Gmail", created_at=1_000.0),
            _row(2, app_name="Chrome", window_title="(3) Inbox - Gmail", created_at=2_000.0),
            _row(3, app_name="Chrome", window_title="(4) Inbox - Gmail", created_at=3_000.0),
            _row(4, app_name="Chrome", window_title="GitHub Issues",  created_at=4_000.0),
        ]
        result, _ = _invoke(["memories", "sessions", "--json"], rows)
        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.output)
        # 2 distinct sessions (Inbox and GitHub Issues), Inbox has 3 captures.
        self.assertEqual(len(payload), 2)
        top = payload[0]
        self.assertEqual(top["capture_count"], 3)
        # Title shown is the most recent variant (the "(4)" one).
        self.assertIn("Inbox", top["title"])

    def test_sorted_by_capture_count_descending(self):
        rows = [
            _row(1, window_title="A", created_at=10.0),
            _row(2, window_title="A", created_at=20.0),
            _row(3, window_title="A", created_at=30.0),
            _row(4, window_title="B", created_at=40.0),
            _row(5, window_title="B", created_at=50.0),
            _row(6, window_title="C", created_at=60.0),
        ]
        result, _ = _invoke(["memories", "sessions", "--json"], rows)
        payload = json.loads(result.output)
        counts = [s["capture_count"] for s in payload]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(counts, [3, 2, 1])

    def test_respects_limit_flag(self):
        rows = [_row(i, window_title=f"Page {i}", created_at=float(i)) for i in range(15)]
        result, _ = _invoke(["memories", "sessions", "-n", "5", "--json"], rows)
        payload = json.loads(result.output)
        self.assertEqual(len(payload), 5)

    def test_empty_day_prints_friendly_message(self):
        result, _ = _invoke(["memories", "sessions"], rows=[])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("No memories", result.output)

    def test_empty_day_json_is_empty_array(self):
        result, _ = _invoke(["memories", "sessions", "--json"], rows=[])
        self.assertEqual(result.output.strip(), "[]")

    def test_skips_captures_with_no_window_title(self):
        """Rows without a window title are not groupable; they should be
        silently skipped, not crash, not appear as a phantom session."""
        rows = [
            _row(1, window_title=""),
            _row(2, window_title="Real Page", created_at=1.0),
            _row(3, window_title="Real Page", created_at=2.0),
        ]
        result, _ = _invoke(["memories", "sessions", "--json"], rows)
        payload = json.loads(result.output)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["capture_count"], 2)

    def test_span_minutes_computed_correctly(self):
        rows = [
            _row(1, window_title="X", created_at=0.0),
            _row(2, window_title="X", created_at=900.0),  # 15 min later
        ]
        result, _ = _invoke(["memories", "sessions", "--json"], rows)
        payload = json.loads(result.output)
        self.assertEqual(payload[0]["span_minutes"], 15.0)

    def test_groups_distinct_apps_separately(self):
        """Same window title in different apps is two different sessions."""
        rows = [
            _row(1, app_name="Chrome", window_title="Settings"),
            _row(2, app_name="Chrome", window_title="Settings"),
            _row(3, app_name="Safari", window_title="Settings"),
        ]
        result, _ = _invoke(["memories", "sessions", "--json"], rows)
        payload = json.loads(result.output)
        self.assertEqual(len(payload), 2)

    def test_human_output_shows_rank_and_count_and_time(self):
        rows = [_row(i, window_title="X", created_at=float(i)) for i in range(3)]
        result, _ = _invoke(["memories", "sessions"], rows)
        self.assertIn("3x", result.output)  # capture count
        self.assertIn("X", result.output)   # title
        self.assertIn("top 1 sessions", result.output)


if __name__ == "__main__":
    unittest.main()
