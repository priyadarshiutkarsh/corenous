"""
Tests for canonical_window_signature and its use in get_recent_for_activity.

Real captures often produce slightly different window titles in
succession ("(3) Inbox", "(4) Inbox", "● main.py", "main.py", etc.)
because of notification counts, unsaved markers, and per app suffixes.
The session clustering logic in daemon.py looks up "recent memories
for this activity" by window title, so any of these variations would
spawn a fresh memory row even though the user was clearly continuing
the same session. The canonical signature collapses these variations.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.summaries import canonical_window_signature


# ── canonical_window_signature ───────────────────────────────────────────

class TestCanonicalWindowSignature(unittest.TestCase):

    # Leading badge counters

    def test_strips_leading_paren_counter(self):
        self.assertEqual(
            canonical_window_signature("(3) Inbox - Gmail"),
            canonical_window_signature("(4) Inbox - Gmail"),
        )

    def test_strips_leading_bracket_counter(self):
        self.assertEqual(
            canonical_window_signature("[5] Slack - team"),
            canonical_window_signature("Slack - team"),
        )

    def test_strips_leading_named_counter(self):
        self.assertEqual(
            canonical_window_signature("(5 new) Newsletter"),
            canonical_window_signature("Newsletter"),
        )
        self.assertEqual(
            canonical_window_signature("(12 unread) Inbox"),
            canonical_window_signature("Inbox"),
        )

    # Trailing badge counters

    def test_strips_trailing_paren_counter(self):
        self.assertEqual(
            canonical_window_signature("Inbox - Gmail (3)"),
            canonical_window_signature("Inbox - Gmail"),
        )

    # Unsaved markers

    def test_strips_leading_unsaved_dot(self):
        self.assertEqual(
            canonical_window_signature("● main.py - corenous"),
            canonical_window_signature("main.py - corenous"),
        )

    def test_strips_leading_asterisk(self):
        self.assertEqual(
            canonical_window_signature("* notes.md - Cursor"),
            canonical_window_signature("notes.md - Cursor"),
        )

    def test_strips_trailing_unsaved_suffix(self):
        self.assertEqual(
            canonical_window_signature("draft (unsaved)"),
            canonical_window_signature("draft"),
        )
        self.assertEqual(
            canonical_window_signature("page (modified)"),
            canonical_window_signature("page"),
        )

    # Combined variations

    def test_strips_both_counter_and_unsaved_marker(self):
        self.assertEqual(
            canonical_window_signature("(3) ● main.py"),
            canonical_window_signature("main.py"),
        )
        self.assertEqual(
            canonical_window_signature("● (3) main.py"),
            canonical_window_signature("main.py"),
        )

    def test_strips_counter_at_both_ends(self):
        self.assertEqual(
            canonical_window_signature("(3) Inbox (5)"),
            canonical_window_signature("Inbox"),
        )

    # False positive guards

    def test_does_not_strip_bare_leading_number_without_brackets(self):
        """Real titles often start with a number: 5 best practices, 10
        things to know, etc. Stripping bare leading digits would erase
        legitimate content."""
        self.assertEqual(
            canonical_window_signature("5 best practices for tokio"),
            "5 best practices for tokio",
        )

    def test_preserves_distinct_titles(self):
        self.assertNotEqual(
            canonical_window_signature("(3) Inbox - Gmail"),
            canonical_window_signature("(3) Sent - Gmail"),
        )
        self.assertNotEqual(
            canonical_window_signature("main.py - corenous"),
            canonical_window_signature("settings.py - corenous"),
        )

    def test_preserves_asterisk_inside_title(self):
        """A * inside the title (e.g. in a glob, an algorithm name, a
        regex sample) must not be stripped. Only a leading * + space
        is the unsaved marker pattern."""
        self.assertEqual(
            canonical_window_signature("regex *.py matched"),
            "regex *.py matched",
        )

    # Edge cases

    def test_empty_input(self):
        self.assertEqual(canonical_window_signature(""), "")
        self.assertEqual(canonical_window_signature(None), "")  # type: ignore[arg-type]

    def test_whitespace_only(self):
        self.assertEqual(canonical_window_signature("   \t\n"), "")

    def test_lowercases_result(self):
        self.assertEqual(
            canonical_window_signature("InBoX - GMAIL"),
            "inbox - gmail",
        )

    def test_is_idempotent(self):
        """Applying the function twice gives the same result as applying
        it once. Important because the function may be called multiple
        times on the same value during a session lookup."""
        for sample in [
            "(3) ● main.py",
            "regular title",
            "(12 unread) Notes (modified)",
            "",
        ]:
            once = canonical_window_signature(sample)
            twice = canonical_window_signature(once)
            self.assertEqual(once, twice, f"not idempotent for {sample!r}")


# ── get_recent_for_activity integration ──────────────────────────────────

class _FakeRow(dict):
    """Mimic the sqlite3.Row object enough for the dict() conversion."""
    def keys(self):  # noqa: D401
        return super().keys()


def _make_store_with_rows(rows: list[dict]):
    """Build a MemoryStore-like mock that returns ``rows`` from the
    fetchall and accepts the canonical-signature filter in Python."""
    from src.memory.store import MemoryStore

    store = MemoryStore.__new__(MemoryStore)
    store._conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = [_FakeRow(r) for r in rows]
    store._conn.execute.return_value = cursor
    return store


class TestGetRecentForActivityCanonicalMatch(unittest.TestCase):

    def test_returns_rows_with_canonical_match(self):
        """A row stored with title '(3) Inbox' must match a lookup for
        '(4) Inbox' — same canonical signature."""
        store = _make_store_with_rows([
            {
                "id": 1, "source": "window", "app_name": "Chrome",
                "bundle_id": "com.google.Chrome", "window_title": "(3) Inbox",
                "created_at": 9_999.0, "is_sensitive": 0, "text_snippet": "",
                "tags": "", "is_starred": 0, "activity": "", "heading": "",
                "summary": "", "narrative": "", "entities": "", "ai_state": "",
                "content_hash": "x",
            },
        ])
        out = store.get_recent_for_activity(
            source="window", app_name="Chrome",
            window_title="(4) Inbox",  # different counter
            bundle_id="com.google.Chrome",
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], 1)

    def test_skips_rows_with_different_canonical_signature(self):
        """Distinct titles still get filtered out — false positive guard."""
        store = _make_store_with_rows([
            {
                "id": 7, "source": "window", "app_name": "Chrome",
                "bundle_id": "com.google.Chrome", "window_title": "(3) Sent",
                "created_at": 9_999.0, "is_sensitive": 0, "text_snippet": "",
                "tags": "", "is_starred": 0, "activity": "", "heading": "",
                "summary": "", "narrative": "", "entities": "", "ai_state": "",
                "content_hash": "y",
            },
        ])
        out = store.get_recent_for_activity(
            source="window", app_name="Chrome",
            window_title="(3) Inbox",  # canonical → "inbox", not "sent"
            bundle_id="com.google.Chrome",
        )
        self.assertEqual(out, [])

    def test_respects_limit_after_canonical_filter(self):
        store = _make_store_with_rows([
            {
                "id": i, "source": "window", "app_name": "Chrome",
                "bundle_id": "com.google.Chrome",
                "window_title": f"({i}) Inbox",
                "created_at": 9_000.0 + i, "is_sensitive": 0, "text_snippet": "",
                "tags": "", "is_starred": 0, "activity": "", "heading": "",
                "summary": "", "narrative": "", "entities": "", "ai_state": "",
                "content_hash": f"h{i}",
            }
            for i in range(1, 11)
        ])
        out = store.get_recent_for_activity(
            source="window", app_name="Chrome",
            window_title="Inbox",
            bundle_id="com.google.Chrome",
            limit=3,
        )
        self.assertEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main()
