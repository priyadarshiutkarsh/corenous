"""Minimal MCP-compatible stdio server for Corenous memories."""
from __future__ import annotations

import json
import sys
from typing import Any

from ..cli.context import AppContext


def _ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _memory_row_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "created_at": float(row.get("created_at") or 0.0),
        "app_name": str(row.get("app_name") or ""),
        "source": str(row.get("source") or ""),
        "heading": str(row.get("heading") or ""),
        "summary": str(row.get("summary") or ""),
        "activity": str(row.get("activity") or ""),
        "window_title": str(row.get("window_title") or ""),
        "text_snippet": str(row.get("text_snippet") or ""),
        "full_text": str(row.get("full_text") or ""),
        "tags": str(row.get("tags") or ""),
    }


def _tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "search_memories",
            "description": "Hybrid search (semantic + keyword) across Corenous memories.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
            },
        },
        {
            "name": "recent_memories",
            "description": "Fetch recent memories in reverse chronological order.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
        },
        {
            "name": "get_memory",
            "description": "Fetch full metadata for one memory by id.",
            "inputSchema": {
                "type": "object",
                "properties": {"memory_id": {"type": "integer", "minimum": 1}},
                "required": ["memory_id"],
            },
        },
    ]


def _call_tool(app: AppContext, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from ..memory.embedder import Embedder
    from ..app.search_combo import combined_search

    if name == "search_memories":
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        top_k = int(arguments.get("top_k") or 10)
        results = combined_search(query, app.store, app.cache, Embedder.get(), top_k=top_k)
        payload = [
            {
                "memory_id": int(r.memory_id),
                "score": float(r.score),
                "created_at": float(r.created_at),
                "app_name": str(r.app_name or ""),
                "heading": str(r.heading or ""),
                "summary": str(r.summary or ""),
                "activity": str(r.activity or ""),
                "text_snippet": str(r.text_snippet or ""),
            }
            for r in results
        ]
        return {"query": query, "count": len(payload), "results": payload}

    if name == "recent_memories":
        limit = int(arguments.get("limit") or 15)
        rows = app.store.get_recent(limit=max(1, min(limit, 100)))
        return {"count": len(rows), "results": [_memory_row_payload(r) for r in rows]}

    if name == "get_memory":
        memory_id = int(arguments.get("memory_id") or 0)
        if memory_id <= 0:
            raise ValueError("memory_id must be > 0")
        row = app.store.get_memory_by_id(memory_id)
        if not row:
            raise ValueError(f"memory {memory_id} not found")
        return {"memory": _memory_row_payload(row)}

    raise ValueError(f"unknown tool: {name}")


def serve_stdio(app: AppContext) -> None:
    """Run a tiny stdio JSON-RPC loop compatible with MCP tool calls."""
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except Exception:
            resp = _err(None, -32700, "parse error")
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}

        try:
            if method == "initialize":
                resp = _ok(
                    req_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "corenous-memory", "version": "0.1.0"},
                        "capabilities": {"tools": {}},
                    },
                )
            elif method == "tools/list":
                resp = _ok(req_id, {"tools": _tool_specs()})
            elif method == "tools/call":
                tool_name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    arguments = {}
                result = _call_tool(app, tool_name, arguments)
                resp = _ok(
                    req_id,
                    {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]},
                )
            elif method == "notifications/initialized":
                continue
            elif method == "ping":
                resp = _ok(req_id, {})
            else:
                resp = _err(req_id, -32601, f"method not found: {method}")
        except ValueError as exc:
            # Caller-fault: malformed arguments. The message is intentional and
            # user-actionable, so surface it under the invalid-params code.
            resp = _err(req_id, -32602, str(exc))
        except Exception as exc:
            # Unexpected server fault. Log the real error internally; return a
            # generic message so paths, SQL fragments, etc. never reach clients.
            print(f"mcp_server internal error: {exc!r}", file=sys.stderr, flush=True)
            resp = _err(req_id, -32603, "internal server error")

        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()

