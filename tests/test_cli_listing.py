"""
Tests for src/cli/list_cmds.py — search, recent, tail.

The CLI's root group calls AppContext.load(Path.cwd()) on every invocation,
which would touch the real config file and SQLite database. We patch that to
inject a MagicMock app context so each test runs in isolation.
"""
from __future__ import annotations

import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.cli.main import cli
from src.cli.list_cmds import _human_row, _json_row, _format_ts


# ── Helpers ───────────────────────────────────────────────────────────────

def _row(
    id: int = 1,
    created_at: float = 1_700_000_000.0,
    app_name: str = "Google Chrome",
    heading: str = "Read about backprop",
    text_snippet: str = "Backpropagation is the algorithm…",
    bm25_score: float | None = None,
    **extra,
) -> dict:
    base = {
        "id": id,
        "created_at": created_at,
        "source": "browser",
        "app_name": app_name,
        "heading": heading,
        "summary": "subject text",
        "text_snippet": text_snippet,
        "activity": "Browsed",
        "window_title": "title",
        "tags": "tag",
        "is_starred": 0,
        "bm25_score": bm25_score,
    }
    base.update(extra)
    return base


def _run_with_mock_store(args: list[str], store: MagicMock) -> "object":
    """Invoke the CLI with AppContext patched to expose a mock store."""
    app = MagicMock()
    app.store = store
    runner = CliRunner()
    with patch("src.cli.main.AppContext.load", return_value=app):
        return runner.invoke(cli, args, catch_exceptions=False)


# ── Formatter helpers ─────────────────────────────────────────────────────

class TestFormatters(unittest.TestCase):

    def test_format_ts_returns_local_string(self):
        # 1_700_000_000 in UTC = 2023-11-14 22:13:20 — but local TZ varies.
        # Just assert the shape (YYYY-MM-DD HH:MM).
        out = _format_ts(1_700_000_000.0)
        self.assertRegex(out, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_human_row_includes_id_app_and_heading(self):
        line = _human_row(_row(id=42, heading="Read paper"))
        self.assertIn("[42]", line)
        self.assertIn("Google Chrome", line)
        self.assertIn("Read paper", line)

    def test_human_row_falls_back_to_snippet_when_no_heading(self):
        line = _human_row(_row(heading="", text_snippet="snippet text"))
        self.assertIn("snippet text", line)

    def test_human_row_hides_score_when_not_requested(self):
        line = _human_row(_row(bm25_score=1.5), show_score=False)
        self.assertNotIn("score=", line)

    def test_human_row_shows_score_when_requested(self):
        line = _human_row(_row(bm25_score=1.5), show_score=True)
        self.assertIn("score=1.50", line)

    def test_human_row_omits_score_when_bm25_missing(self):
        line = _human_row(_row(bm25_score=None), show_score=True)
        self.assertNotIn("score=", line)

    def test_json_row_has_required_fields(self):
        d = _json_row(_row(id=7, is_starred=1, bm25_score=2.3))
        self.assertEqual(d["id"], 7)
        self.assertTrue(d["starred"])
        self.assertEqual(d["bm25_score"], 2.3)
        self.assertIn("created_human", d)
        self.assertIn("snippet", d)


# ── search ────────────────────────────────────────────────────────────────

class TestSearchCommand(unittest.TestCase):

    def test_calls_fts_search_with_joined_terms(self):
        store = MagicMock()
        store.fts_search.return_value = []
        result = _run_with_mock_store(["search", "github", "vector"], store)
        self.assertEqual(result.exit_code, 0)
        store.fts_search.assert_called_once()
        call_args = store.fts_search.call_args
        self.assertEqual(call_args[0][0], "github vector")

    def test_respects_limit_flag(self):
        store = MagicMock()
        store.fts_search.return_value = []
        _run_with_mock_store(["search", "x", "--limit", "5"], store)
        self.assertEqual(store.fts_search.call_args[1]["limit"], 5)

    def test_human_output_contains_score(self):
        store = MagicMock()
        store.fts_search.return_value = [_row(bm25_score=1.5)]
        result = _run_with_mock_store(["search", "foo"], store)
        self.assertIn("score=1.50", result.output)

    def test_empty_results_print_friendly_message(self):
        store = MagicMock()
        store.fts_search.return_value = []
        result = _run_with_mock_store(["search", "noresults"], store)
        self.assertIn("No memories", result.output)

    def test_json_output_is_valid_json_list(self):
        store = MagicMock()
        store.fts_search.return_value = [_row(id=1), _row(id=2)]
        result = _run_with_mock_store(["search", "x", "--json"], store)
        payload = json.loads(result.output)
        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 2)
        self.assertEqual({r["id"] for r in payload}, {1, 2})

    def test_json_output_empty_is_empty_list_not_error(self):
        """`search foo --json` with no hits must emit `[]`, not the human
        'No memories found.' string — otherwise scripts can't parse it."""
        store = MagicMock()
        store.fts_search.return_value = []
        result = _run_with_mock_store(["search", "x", "--json"], store)
        self.assertEqual(json.loads(result.output), [])

    def test_missing_terms_arg_fails(self):
        """Calling `search` with no terms should fail (required argument)."""
        store = MagicMock()
        result = _run_with_mock_store(["search"], store)
        self.assertNotEqual(result.exit_code, 0)


# ── recent ────────────────────────────────────────────────────────────────

class TestRecentCommand(unittest.TestCase):

    def test_calls_get_recent_with_default_limit(self):
        store = MagicMock()
        store.get_recent.return_value = []
        _run_with_mock_store(["recent"], store)
        store.get_recent.assert_called_once_with(limit=20)

    def test_respects_limit_flag(self):
        store = MagicMock()
        store.get_recent.return_value = []
        _run_with_mock_store(["recent", "-n", "3"], store)
        store.get_recent.assert_called_once_with(limit=3)

    def test_human_output_lists_rows(self):
        store = MagicMock()
        store.get_recent.return_value = [
            _row(id=10, heading="First"),
            _row(id=11, heading="Second"),
        ]
        result = _run_with_mock_store(["recent"], store)
        self.assertIn("First", result.output)
        self.assertIn("Second", result.output)
        # Recent does NOT show a relevance score (no FTS match).
        self.assertNotIn("score=", result.output)

    def test_json_output_serializes_all_rows(self):
        store = MagicMock()
        store.get_recent.return_value = [_row(id=1), _row(id=2), _row(id=3)]
        result = _run_with_mock_store(["recent", "--json"], store)
        payload = json.loads(result.output)
        self.assertEqual(len(payload), 3)


# ── tail ──────────────────────────────────────────────────────────────────

class TestTailCommand(unittest.TestCase):
    """tail polls get_recent in a loop. We escape the loop by making
    time.sleep raise KeyboardInterrupt after the first iteration."""

    def test_only_emits_rows_newer_than_bootstrap(self):
        store = MagicMock()
        # Bootstrap call returns max-id=5; loop call returns 5, 6, 7.
        # Only 6 and 7 should appear in output.
        store.get_recent.side_effect = [
            [_row(id=5)],                                      # bootstrap
            [_row(id=7), _row(id=6), _row(id=5)],              # first poll
        ]
        with patch("src.cli.list_cmds.time.sleep", side_effect=KeyboardInterrupt):
            result = _run_with_mock_store(["tail"], store)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("[6]", result.output)
        self.assertIn("[7]", result.output)
        self.assertNotIn("[5]", result.output)

    def test_chronological_order_in_output(self):
        """Rows must appear oldest-to-newest in tail (like `tail -f`),
        even though get_recent returns newest-first."""
        store = MagicMock()
        store.get_recent.side_effect = [
            [],                                                # bootstrap (empty store)
            [_row(id=3), _row(id=2), _row(id=1)],              # first poll
        ]
        with patch("src.cli.list_cmds.time.sleep", side_effect=KeyboardInterrupt):
            result = _run_with_mock_store(["tail"], store)
        # Find the indices of "[1]", "[2]", "[3]" in the output and assert order.
        i1 = result.output.find("[1]")
        i2 = result.output.find("[2]")
        i3 = result.output.find("[3]")
        self.assertGreater(i1, -1)
        self.assertLess(i1, i2)
        self.assertLess(i2, i3)

    def test_json_mode_emits_ndjson_not_array(self):
        """In --json mode, tail must emit newline-delimited JSON objects
        (one per row), NOT a single JSON array. This is the contract for
        streaming consumers (jq -c, log aggregators)."""
        store = MagicMock()
        store.get_recent.side_effect = [
            [],
            [_row(id=2, heading="b"), _row(id=1, heading="a")],
        ]
        with patch("src.cli.list_cmds.time.sleep", side_effect=KeyboardInterrupt):
            result = _run_with_mock_store(["tail", "--json"], store)
        lines = [ln for ln in result.output.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        for ln in lines:
            parsed = json.loads(ln)  # each line must parse as a JSON object
            self.assertIn("id", parsed)


if __name__ == "__main__":
    unittest.main()
