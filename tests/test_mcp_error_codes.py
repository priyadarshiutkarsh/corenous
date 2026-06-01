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
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent import mcp_server


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


if __name__ == "__main__":
    unittest.main()
