"""
Tests for src/routines/detector.py and src/routines/executor.py.
Run with: python -m pytest tests/test_routines.py -v
"""
from __future__ import annotations

import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.routines.detector import (
    SuggestedRoutine,
    detect_routines,
    _detect_app_routines,
    _detect_url_routines,
    _extract_domain,
    _time_label,
)
from src.routines.executor import execute_routine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(day_offset: int, hour: int) -> float:
    """Return a Unix timestamp for `day_offset` days ago at `hour:00`."""
    base = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    return (base - timedelta(days=day_offset)).timestamp()


def _make_store(rows: list[dict]) -> MagicMock:
    """Build a minimal MemoryStore mock that returns `rows` from _fetch."""
    store = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = [_DictRow(r) for r in rows]
    store._conn.execute.return_value = cursor
    return store


class _DictRow(dict):
    """Mimic sqlite3.Row (supports dict(r))."""
    def keys(self):
        return super().keys()


# ---------------------------------------------------------------------------
# detector._time_label
# ---------------------------------------------------------------------------

class TestTimeLabel(unittest.TestCase):
    def test_morning(self):
        self.assertEqual(_time_label(8), "morning")

    def test_afternoon(self):
        self.assertEqual(_time_label(14), "afternoon")

    def test_evening(self):
        self.assertEqual(_time_label(19), "evening")

    def test_night(self):
        self.assertEqual(_time_label(2), "night")


# ---------------------------------------------------------------------------
# detector._extract_domain
# ---------------------------------------------------------------------------

class TestExtractDomain(unittest.TestCase):
    def test_plain_domain(self):
        self.assertEqual(_extract_domain("Browsed github.com"), "github.com")

    def test_with_www(self):
        self.assertEqual(_extract_domain("https://www.notion.so/dashboard"), "notion.so")

    def test_noise_domain_filtered(self):
        self.assertEqual(_extract_domain("apple.com update"), "")

    def test_empty(self):
        self.assertEqual(_extract_domain(""), "")

    def test_no_domain(self):
        self.assertEqual(_extract_domain("just some text"), "")


# ---------------------------------------------------------------------------
# detector._detect_app_routines
# ---------------------------------------------------------------------------

class TestDetectAppRoutines(unittest.TestCase):

    def _rows_for_app(self, app: str, n_days: int, hour: int) -> list[dict]:
        return [
            {
                "app_name": app,
                "activity": "",
                "window_title": "",
                "created_at": _ts(i, hour),
                "source": "window",
            }
            for i in range(n_days)
        ]

    def test_detects_consistent_app(self):
        rows = self._rows_for_app("Slack", 5, 9)
        routines = _detect_app_routines(rows, min_days=3)
        self.assertTrue(any(r.action_data == "Slack" for r in routines))

    def test_ignores_too_few_days(self):
        rows = self._rows_for_app("Slack", 2, 9)
        routines = _detect_app_routines(rows, min_days=3)
        self.assertFalse(any(r.action_data == "Slack" for r in routines))

    def test_ignores_ignored_apps(self):
        rows = self._rows_for_app("Finder", 7, 9)
        routines = _detect_app_routines(rows, min_days=3)
        self.assertFalse(routines)

    def test_confidence_increases_with_days(self):
        r3 = _detect_app_routines(self._rows_for_app("VSCode", 3, 10), min_days=3)
        r7 = _detect_app_routines(self._rows_for_app("VSCode", 7, 10), min_days=3)
        if r3 and r7:
            self.assertGreaterEqual(r7[0].confidence, r3[0].confidence)

    def test_scattered_hours_not_detected(self):
        """If the user opens an app at wildly different times, skip it."""
        from datetime import timedelta
        rows = []
        hours = [7, 14, 22, 3, 11, 18, 2]  # completely random
        for i, h in enumerate(hours):
            rows.append({
                "app_name": "Chaos",
                "activity": "",
                "window_title": "",
                "created_at": _ts(i, h),
                "source": "window",
            })
        routines = _detect_app_routines(rows, min_days=3)
        self.assertFalse(any(r.action_data == "Chaos" for r in routines))


# ---------------------------------------------------------------------------
# detector._detect_url_routines
# ---------------------------------------------------------------------------

class TestDetectUrlRoutines(unittest.TestCase):

    def _rows_for_domain(self, domain: str, n_days: int, hour: int) -> list[dict]:
        return [
            {
                "app_name": "Google Chrome",
                "activity": f"Browsed {domain}",
                "window_title": f"Page on {domain}",
                "created_at": _ts(i, hour),
                "source": "browser",
            }
            for i in range(n_days)
        ]

    def test_detects_consistent_url(self):
        rows = self._rows_for_domain("github.com", 5, 10)
        routines = _detect_url_routines(rows, min_days=3)
        self.assertTrue(any("github.com" in r.action_data for r in routines))

    def test_action_type_is_open_url(self):
        rows = self._rows_for_domain("notion.so", 4, 11)
        routines = _detect_url_routines(rows, min_days=3)
        if routines:
            self.assertEqual(routines[0].action_type, "open_url")

    def test_url_prefixed_with_https(self):
        rows = self._rows_for_domain("linear.app", 4, 9)
        routines = _detect_url_routines(rows, min_days=3)
        if routines:
            self.assertTrue(routines[0].action_data.startswith("https://"))

    def test_double_field_match_does_not_skew_avg_hour(self):
        """When the same domain appears in both activity and window_title on
        some days but only one field on others, the average hour must be
        weighted equally per calendar day — not double-weighted for the days
        where both fields match.

        Construction (all same domain, min_days=5):
          Days 0,2,4 → domain in BOTH fields at 9 am
          Days 1,3   → domain in activity ONLY at 13 pm

        Correct (per-day) avg_h = (9+13+9+13+9)/5 = 10.6
        Broken  (per-entry) avg_h = (9+9 + 13 + 9+9 + 13 + 9+9)/8 = 80/8 = 10.0

        The two values are different, so asserting the correct one fails on
        the old implementation.
        """
        rows = []
        for i in range(5):
            hour = 9 if i % 2 == 0 else 13
            in_window = i % 2 == 0  # even days: domain in both fields
            rows.append({
                "app_name": "Google Chrome",
                "activity": f"Browsed github.com",
                "window_title": "GitHub — github.com" if in_window else "Pull requests",
                "created_at": _ts(i, hour),
                "source": "browser",
            })
        routines = _detect_url_routines(rows, min_days=3)
        github = next((r for r in routines if "github.com" in r.action_data), None)
        self.assertIsNotNone(github, "github.com routine not detected")
        expected_avg = (9 + 13 + 9 + 13 + 9) / 5  # 10.6 — one entry per day
        self.assertAlmostEqual(github.time_of_day_hour, expected_avg, places=6)


# ---------------------------------------------------------------------------
# detector.detect_routines (integration)
# ---------------------------------------------------------------------------

class TestDetectRoutines(unittest.TestCase):

    def _make_rich_store(self) -> MagicMock:
        rows = []
        # Slack opened at 9am for 5 consecutive days
        for i in range(5):
            rows.append({
                "app_name": "Slack",
                "activity": "",
                "window_title": "Slack",
                "created_at": _ts(i, 9),
                "source": "window",
            })
        # GitHub visited at 10am for 4 days
        for i in range(4):
            rows.append({
                "app_name": "Google Chrome",
                "activity": "Browsed github.com",
                "window_title": "GitHub",
                "created_at": _ts(i, 10),
                "source": "browser",
            })
        return _make_store(rows)

    def test_returns_list(self):
        store = self._make_rich_store()
        result = detect_routines(store, min_days=3, lookback_days=14)
        self.assertIsInstance(result, list)

    def test_max_5_results(self):
        store = self._make_rich_store()
        result = detect_routines(store, min_days=3, lookback_days=14)
        self.assertLessEqual(len(result), 5)

    def test_empty_store_returns_empty(self):
        store = _make_store([])
        result = detect_routines(store, min_days=3, lookback_days=14)
        self.assertEqual(result, [])

    def test_routine_has_required_fields(self):
        store = self._make_rich_store()
        result = detect_routines(store, min_days=3, lookback_days=14)
        for r in result:
            self.assertIsInstance(r.id, str)
            self.assertIsInstance(r.title, str)
            self.assertIsInstance(r.description, str)
            self.assertIn(r.action_type, ("open_app", "open_url"))
            self.assertIsInstance(r.action_data, str)
            self.assertGreater(len(r.action_data), 0)


# ---------------------------------------------------------------------------
# executor
# ---------------------------------------------------------------------------

class TestExecuteRoutine(unittest.TestCase):

    @patch("src.routines.executor.subprocess.run")
    def test_open_app_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        result = execute_routine("open_app", "Slack")
        self.assertTrue(result)
        mock_run.assert_called_once()
        self.assertIn("Slack", mock_run.call_args[0][0])

    @patch("src.routines.executor.subprocess.run")
    def test_open_app_fallback(self, mock_run):
        # First call fails, second (fallback) succeeds
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0),
        ]
        result = execute_routine("open_app", "SomeApp")
        self.assertTrue(result)
        self.assertEqual(mock_run.call_count, 2)

    @patch("src.routines.executor.webbrowser.open")
    def test_open_url(self, mock_open):
        result = execute_routine("open_url", "https://github.com")
        self.assertTrue(result)
        mock_open.assert_called_once_with("https://github.com")

    @patch("src.routines.executor.webbrowser.open")
    def test_open_url_adds_scheme(self, mock_open):
        execute_routine("open_url", "github.com")
        mock_open.assert_called_once_with("https://github.com")

    def test_unknown_action_type_returns_false(self):
        result = execute_routine("teleport", "Mars")
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# SuggestedRoutine.make_id stability
# ---------------------------------------------------------------------------

class TestRoutineId(unittest.TestCase):
    def test_stable_across_calls(self):
        id1 = SuggestedRoutine.make_id("open_app", "Slack", 9)
        id2 = SuggestedRoutine.make_id("open_app", "Slack", 9)
        self.assertEqual(id1, id2)

    def test_different_for_different_inputs(self):
        id1 = SuggestedRoutine.make_id("open_app", "Slack", 9)
        id2 = SuggestedRoutine.make_id("open_app", "Notion", 9)
        self.assertNotEqual(id1, id2)

    def test_case_insensitive(self):
        id1 = SuggestedRoutine.make_id("open_app", "Slack", 9)
        id2 = SuggestedRoutine.make_id("open_app", "SLACK", 9)
        self.assertEqual(id1, id2)


if __name__ == "__main__":
    unittest.main()
