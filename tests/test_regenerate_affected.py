"""
Tests for `corenous-ai memories regenerate-affected`.

Detects memory rows whose stored narrative contains chrome leak
signatures (version strings, update banners) from before
strip_ui_chrome shipped, and reruns ai_memory_bullets on them.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.cli.main import cli, _LEGACY_CHROME_NARRATIVE_RE


# ── Detection regex ──────────────────────────────────────────────────────

class TestChromeNarrativeDetection(unittest.TestCase):

    def test_matches_version_string_in_sentence(self):
        """The original bug example: 'create a PR to update v1.9255.0'."""
        self.assertTrue(_LEGACY_CHROME_NARRATIVE_RE.search(
            "a possible follow-up was to create a PR to update v1.9255.0"
        ))

    def test_matches_relaunch_to_update_phrase(self):
        self.assertTrue(_LEGACY_CHROME_NARRATIVE_RE.search(
            "the page showed Relaunch to update notice"
        ))

    def test_matches_update_available_banner(self):
        self.assertTrue(_LEGACY_CHROME_NARRATIVE_RE.search(
            "an Update available banner was visible at the top"
        ))

    def test_does_not_match_clean_narrative(self):
        self.assertIsNone(_LEGACY_CHROME_NARRATIVE_RE.search(
            "the user reviewed the new CLI commands they added to corenous"
        ))

    def test_does_not_match_other_version_like_numbers(self):
        """'Python 3.13.7' should not look like a chrome leak. The regex
        requires the literal 'v' prefix to avoid false positives on
        version numbers that appear naturally in technical content."""
        self.assertIsNone(_LEGACY_CHROME_NARRATIVE_RE.search(
            "the user was running Python 3.13.7 on macOS 25.2.0"
        ))


# ── CLI command end-to-end (mocked AI + store) ───────────────────────────

def _row(id: int, narrative: str, full_text: str = "real content text") -> dict:
    return {
        "id": id,
        "app_name": "Claude",
        "window_title": "Window",
        "activity": "Read screen",
        "heading": "Some heading",
        "narrative": narrative,
        "full_text": full_text,
    }


def _make_store(rows: list[dict]) -> MagicMock:
    store = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    store._conn.execute.return_value = cursor
    store.update_ai.return_value = True
    return store


def _invoke(args: list[str], rows: list[dict], ai_output: str = "• Clean bullet."):
    """Run the CLI with a mocked AppContext and a stubbed AI call."""
    app = MagicMock()
    app.store = _make_store(rows)
    runner = CliRunner()
    with patch("src.cli.main.AppContext.load", return_value=app), \
         patch("src.ai.llm.load_model_sync", return_value=True), \
         patch("src.ai.summarizer.ai_memory_bullets", return_value=ai_output) as mock_ai:
        result = runner.invoke(cli, args, catch_exceptions=False)
    return result, app, mock_ai


class TestRegenerateAffectedCommand(unittest.TestCase):

    def test_dry_run_lists_affected_without_writing(self):
        rows = [
            _row(1, narrative="contains v1.9255.0"),
            _row(2, narrative="clean narrative"),
        ]
        result, app, mock_ai = _invoke(
            ["memories", "regenerate-affected", "--dry-run"], rows
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("[1]", result.output)
        self.assertNotIn("[2]", result.output)
        mock_ai.assert_not_called()
        app.store.update_ai.assert_not_called()

    def test_reports_nothing_to_do_when_no_chrome(self):
        rows = [_row(1, narrative="clean text")]
        result, app, mock_ai = _invoke(
            ["memories", "regenerate-affected"], rows
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("No memories", result.output)
        mock_ai.assert_not_called()
        app.store.update_ai.assert_not_called()

    def test_writes_new_narrative_when_ai_returns_content(self):
        rows = [_row(7, narrative="v1.2.3 leaked")]
        result, app, mock_ai = _invoke(
            ["memories", "regenerate-affected"], rows,
            ai_output="• Clean replacement bullet."
        )
        self.assertEqual(result.exit_code, 0)
        mock_ai.assert_called_once()
        app.store.update_ai.assert_called_once_with(
            7, narrative="• Clean replacement bullet."
        )

    def test_skips_when_full_text_is_missing(self):
        """Without full_text we cannot re-run AI, so the row stays as is."""
        rows = [_row(1, narrative="v1.0.0 leak", full_text="")]
        result, app, mock_ai = _invoke(
            ["memories", "regenerate-affected"], rows
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("no full_text", result.output)
        app.store.update_ai.assert_not_called()

    def test_skips_when_ai_returns_empty(self):
        """An empty AI response must NOT overwrite the existing narrative."""
        rows = [_row(1, narrative="v9.9.9 leak")]
        result, app, mock_ai = _invoke(
            ["memories", "regenerate-affected"], rows, ai_output=""
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("AI returned empty", result.output)
        app.store.update_ai.assert_not_called()

    def test_limit_caps_processing(self):
        rows = [
            _row(i, narrative=f"v1.0.{i} leak") for i in range(5)
        ]
        result, app, mock_ai = _invoke(
            ["memories", "regenerate-affected", "--limit", "2"], rows,
            ai_output="• new"
        )
        self.assertEqual(result.exit_code, 0)
        # Only 2 update_ai calls despite 5 affected memories
        self.assertEqual(app.store.update_ai.call_count, 2)


if __name__ == "__main__":
    unittest.main()
