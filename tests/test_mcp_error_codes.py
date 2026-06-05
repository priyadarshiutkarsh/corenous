"""
Tests for the Corenous MCP server's error reporting (FastMCP).

Regression target: an unexpected exception used to be returned verbatim to
the client, leaking file paths, SQL fragments, and DB schema. The server now
routes caller-fault ``ValueError`` through with its actionable message, while
every other exception is logged to stderr only and surfaced to the client as
a generic "internal server error". Tool errors are delivered as MCP tool
errors (the in-process ``call_tool`` raises; the protocol layer serialises the
same message into ``isError`` content)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent import mcp_server


def _ok(app, name: str, arguments: dict) -> dict:
    """Invoke a tool that should succeed; return its parsed JSON payload."""
    server = mcp_server.build_server(app)
    result = asyncio.run(server.call_tool(name, arguments))
    # FastMCP returns (content_list, ...) or content_list; pull the text out.
    content = result[0] if isinstance(result, tuple) else result
    text = content[0].text if isinstance(content, (list, tuple)) else content.text
    return json.loads(text)


def _call(app, name: str, arguments: dict) -> Exception:
    """Invoke a tool and return the exception it raises (fails if none)."""
    server = mcp_server.build_server(app)

    async def run():
        await server.call_tool(name, arguments)

    try:
        with mock.patch.object(sys, "stderr", os.fdopen(os.open(os.devnull, os.O_WRONLY), "w")):
            asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 — that's the thing under test
        return exc
    raise AssertionError(f"{name} did not raise")


class TestMcpErrorReporting(unittest.TestCase):

    def test_caller_fault_keeps_actionable_message(self):
        # Empty query is the caller's fault — the message must reach them.
        exc = _call(mock.MagicMock(), "search_memories", {"query": "  "})
        self.assertIn("query must not be empty", str(exc))

    def test_missing_memory_keeps_actionable_message(self):
        app = mock.MagicMock()
        app.store.get_memory_by_id.return_value = None
        exc = _call(app, "get_memory", {"memory_id": 4242})
        self.assertIn("4242 not found", str(exc))

    def test_unexpected_error_is_generic_and_does_not_leak(self):
        secret = "/Users/secret/path/memories.db: no such table: foo"
        app = mock.MagicMock()
        app.store.get_recent.side_effect = RuntimeError(secret)
        exc = _call(app, "list_recent_memories", {})
        msg = str(exc)
        self.assertIn("internal server error", msg)
        # The leaked detail must never reach the client.
        self.assertNotIn("secret", msg)
        self.assertNotIn("no such table", msg)


class TestMcpNewTools(unittest.TestCase):
    _ROW = {"id": 7, "created_at": 1000.0, "app_name": "Chrome", "source": "screen",
            "heading": "h", "summary": "s", "text_snippet": "snip"}

    def test_memories_in_timeframe(self):
        app = mock.MagicMock()
        app.store.get_memories_in_range.return_value = [self._ROW]
        out = _ok(app, "memories_in_timeframe", {"days": 3})
        self.assertEqual(out["days"], 3)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["results"][0]["id"], 7)

    def test_list_starred_memories(self):
        app = mock.MagicMock()
        app.store.get_starred.return_value = [self._ROW]
        out = _ok(app, "list_starred_memories", {})
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["results"][0]["id"], 7)

    def test_get_daily_digest_present(self):
        app = mock.MagicMock()
        app.store.get_digest.return_value = {
            "content": "you did things", "source_count": 12, "generated_at": 5.0,
        }
        out = _ok(app, "get_daily_digest", {"day": "today"})
        self.assertEqual(out["digest"], "you did things")
        self.assertEqual(out["source_count"], 12)

    def test_get_daily_digest_absent(self):
        app = mock.MagicMock()
        app.store.get_digest.return_value = None
        out = _ok(app, "get_daily_digest", {"day": "2026-01-01"})
        self.assertIsNone(out["digest"])
        self.assertEqual(out["day"], "2026-01-01")


if __name__ == "__main__":
    unittest.main()
