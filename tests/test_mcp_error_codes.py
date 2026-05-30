"""
Tests for MCP stdio server error reporting.

Regression target: every unexpected exception was returned as code -32000
with str(exc) as the message, leaking file paths, SQL fragments, and
internal dict shape to JSON-RPC clients. The fix routes caller-fault
ValueError to -32602 (invalid params) with its actionable message, and
unexpected exceptions to -32603 (internal error) with a generic message
while logging the real error to stderr.
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent import mcp_server


def _drive(request: dict, app) -> dict:
    """Feed one JSON-RPC request through serve_stdio, return the response."""
    stdin = io.StringIO(json.dumps(request) + "\n")
    stdout = io.StringIO()
    with mock.patch.object(sys, "stdin", stdin), \
         mock.patch.object(sys, "stdout", stdout), \
         mock.patch.object(sys, "stderr", io.StringIO()):
        mcp_server.serve_stdio(app)
    return json.loads(stdout.getvalue().strip())


class TestMcpErrorCodes(unittest.TestCase):

    def test_caller_fault_is_invalid_params(self):
        app = mock.MagicMock()
        resp = _drive(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "search_memories", "arguments": {"query": ""}}},
            app,
        )
        self.assertEqual(resp["error"]["code"], -32602)
        self.assertIn("query is required", resp["error"]["message"])

    def test_unexpected_error_is_generic_internal(self):
        secret = "/Users/secret/path/memories.db: no such table: foo"
        app = mock.MagicMock()
        app.store.get_recent.side_effect = RuntimeError(secret)
        resp = _drive(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "recent_memories", "arguments": {}}},
            app,
        )
        self.assertEqual(resp["error"]["code"], -32603)
        self.assertEqual(resp["error"]["message"], "internal server error")
        # The leaked detail must never reach the client.
        self.assertNotIn("secret", json.dumps(resp))
        self.assertNotIn("no such table", json.dumps(resp))


if __name__ == "__main__":
    unittest.main()
