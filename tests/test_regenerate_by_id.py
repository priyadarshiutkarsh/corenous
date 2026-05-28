"""
Tests for `corenous-ai memories regenerate <id>` — force AI refinement
on a single memory by id, regardless of whether it matches any of the
chrome leak patterns that `regenerate-affected` detects.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.cli.main import cli


def _row(
    id: int = 1,
    app_name: str = "Google Chrome",
    window_title: str = "Some Window",
    activity: str = "Browsed",
    heading: str = "Some heading",
    narrative: str = "old narrative",
    full_text: str = "the page had real prose with several sentences.",
) -> dict:
    return {
        "id": id,
        "app_name": app_name,
        "window_title": window_title,
        "activity": activity,
        "heading": heading,
        "narrative": narrative,
        "full_text": full_text,
    }


def _make_store(row: dict | None) -> MagicMock:
    store = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    store._conn.execute.return_value = cursor
    store.update_ai.return_value = True
    return store


def _invoke(args: list[str], row: dict | None, ai_output: str = "• new bullet."):
    app = MagicMock()
    app.store = _make_store(row)
    runner = CliRunner()
    with patch("src.cli.main.AppContext.load", return_value=app), \
         patch("src.ai.llm.load_model_sync", return_value=True), \
         patch("src.ai.summarizer.ai_memory_bullets", return_value=ai_output) as mock_ai:
        result = runner.invoke(cli, args, catch_exceptions=False)
    return result, app, mock_ai


class TestRegenerateById(unittest.TestCase):

    def test_writes_new_narrative_on_success(self):
        row = _row(id=42, narrative="old")
        result, app, mock_ai = _invoke(
            ["memories", "regenerate", "42"], row, ai_output="• Clean output."
        )
        self.assertEqual(result.exit_code, 0)
        mock_ai.assert_called_once()
        app.store.update_ai.assert_called_once_with(42, narrative="• Clean output.")

    def test_passes_metadata_through_to_ai(self):
        """The id alone is not enough; ai_memory_bullets needs heading,
        window_title, app_name, and activity for the contextual fallback
        and prompt rules to behave correctly."""
        row = _row(
            id=7,
            app_name="Safari",
            window_title="Pull request #99",
            activity="Browsed github.com",
            heading="Reviewed PR",
        )
        result, app, mock_ai = _invoke(["memories", "regenerate", "7"], row)
        self.assertEqual(result.exit_code, 0)
        kwargs = mock_ai.call_args.kwargs
        self.assertEqual(kwargs["app_name"], "Safari")
        self.assertEqual(kwargs["window_title"], "Pull request #99")
        self.assertEqual(kwargs["activity"], "Browsed github.com")
        self.assertEqual(kwargs["heading"], "Reviewed PR")

    def test_dry_run_does_not_call_ai_or_write(self):
        row = _row(id=10)
        result, app, mock_ai = _invoke(
            ["memories", "regenerate", "10", "--dry-run"], row
        )
        self.assertEqual(result.exit_code, 0)
        mock_ai.assert_not_called()
        app.store.update_ai.assert_not_called()

    def test_memory_not_found_exits_with_error(self):
        result, app, mock_ai = _invoke(["memories", "regenerate", "999"], None)
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("not found", result.output.lower())
        mock_ai.assert_not_called()

    def test_empty_full_text_exits_with_error(self):
        """If the row exists but has no captured text, regeneration would
        produce empty output. Surface the reason instead of silently
        clobbering the narrative."""
        row = _row(id=12, full_text="")
        result, app, mock_ai = _invoke(["memories", "regenerate", "12"], row)
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("no full_text", result.output.lower())
        mock_ai.assert_not_called()
        app.store.update_ai.assert_not_called()

    def test_empty_ai_response_preserves_existing_narrative(self):
        """AI returning '' must NOT overwrite the existing narrative with
        an empty string; raise so the user knows."""
        row = _row(id=15, narrative="something existing")
        result, app, mock_ai = _invoke(
            ["memories", "regenerate", "15"], row, ai_output=""
        )
        self.assertNotEqual(result.exit_code, 0)
        app.store.update_ai.assert_not_called()


if __name__ == "__main__":
    unittest.main()
