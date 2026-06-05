"""Corenous memory MCP server, built on the official MCP Python SDK (FastMCP).

Exposes a read-only view of the local memory store to AI agents over stdio:
agents can search, read, and traverse memories, but never mutate them. All
tools are stateless — each call is independent and self-contained.

Wired to the ``corenous-ai agent serve`` command via :func:`serve_stdio`.
"""
from __future__ import annotations

import functools
import json
import sys
from datetime import datetime, timezone
from typing import Annotated, Any, Callable

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..cli.context import AppContext


def _guard(fn: Callable) -> Callable:
    """Wrap a tool/resource so unexpected failures never leak internals.

    A ``ValueError`` is treated as caller-actionable and its message passes
    through unchanged (e.g. "memory 42 not found"). Any other exception is a
    server fault: the real error — which may carry file paths, SQL, or the
    DB schema — is logged to stderr only, and the client sees a generic
    message instead."""
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 — sanitised on purpose
            print(f"mcp_server internal error in {fn.__name__}: {exc!r}",
                  file=sys.stderr, flush=True)
            raise ValueError("internal server error") from None
    return wrapper


def _memory_row_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten a store row into a stable JSON-serialisable shape."""
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


def build_server(app: AppContext) -> FastMCP:
    """Construct the FastMCP server with tools bound to ``app``'s store.

    Tools are registered as closures over ``app`` so the long-lived
    :class:`AppContext` (and its cached store + vector index) is opened once
    and reused across calls, rather than per request."""
    mcp = FastMCP(
        "corenous-memory",
        instructions=(
            "Read-only access to the user's local Corenous second brain — "
            "screen captures, clipboard, and browsing distilled into memories. "
            "Use search_memories for topical lookup, list_recent_memories to "
            "see what the user did lately, get_memory to read one in full, "
            "find_related_memories to follow a thread, memories_in_timeframe for "
            "time-scoped questions, list_starred_memories for the user's curated "
            "items, and get_daily_digest for a day's recap. Nothing here is mutable."
        ),
    )

    @mcp.tool()
    @_guard
    def search_memories(
        query: Annotated[str, Field(description="Natural-language search query, e.g. 'VRAM for local models'.")],
        limit: Annotated[int, Field(default=10, ge=1, le=50, description="Maximum number of memories to return.")] = 10,
    ) -> str:
        """Hybrid (semantic + keyword) search across the user's memories.

        Use this when you need memories about a topic, regardless of when they
        were captured. Returns memories ranked by relevance, each with its id,
        relevance score, capture time, app, heading, summary, and a snippet."""
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        from ..memory.embedder import Embedder
        from ..app.search_combo import combined_search

        results = combined_search(
            query, app.store, app.cache, Embedder.get(), top_k=limit,
        )
        payload = [
            {
                "memory_id": int(r.memory_id),
                "score": round(float(r.score), 4),
                "created_at": float(r.created_at),
                "app_name": str(r.app_name or ""),
                "heading": str(r.heading or ""),
                "summary": str(r.summary or ""),
                "activity": str(r.activity or ""),
                "text_snippet": str(r.text_snippet or ""),
            }
            for r in results
        ]
        return json.dumps(
            {"query": query, "count": len(payload), "results": payload},
            ensure_ascii=False,
        )

    @mcp.tool()
    @_guard
    def list_recent_memories(
        limit: Annotated[int, Field(default=15, ge=1, le=100, description="How many recent memories to return.")] = 15,
    ) -> str:
        """List the most recent memories in reverse chronological order.

        Use this to see what the user has been doing lately when no specific
        search topic is known. Returns full memory metadata for each row."""
        rows = app.store.get_recent(limit=limit)
        return json.dumps(
            {"count": len(rows), "results": [_memory_row_payload(r) for r in rows]},
            ensure_ascii=False,
        )

    @mcp.tool()
    @_guard
    def get_memory(
        memory_id: Annotated[int, Field(ge=1, description="The id of the memory to fetch (from a search or recent result).")],
    ) -> str:
        """Fetch the full content and metadata for a single memory by id.

        Use this after a search or recent listing to read a memory's complete
        captured text. Raises if the id does not exist or is private."""
        row = app.store.get_memory_by_id(memory_id)
        if not row or int(row.get("is_sensitive") or 0):
            raise ValueError(f"memory {memory_id} not found")
        return json.dumps({"memory": _memory_row_payload(row)}, ensure_ascii=False)

    @mcp.tool()
    @_guard
    def find_related_memories(
        memory_id: Annotated[int, Field(ge=1, description="The id of the memory whose neighbours you want.")],
        limit: Annotated[int, Field(default=5, ge=1, le=20, description="Maximum number of related memories to return.")] = 5,
    ) -> str:
        """Find memories semantically nearest to a given memory.

        Use this to follow a thread — given one memory, surface others about
        the same subject. Returns neighbours ordered by similarity (id,
        heading, capture time, score), excluding the source and near-duplicates."""
        related = _related_memories(app, memory_id, limit=limit)
        return json.dumps(
            {"memory_id": memory_id, "count": len(related), "results": related},
            ensure_ascii=False,
        )

    @mcp.tool()
    @_guard
    def memories_in_timeframe(
        days: Annotated[int, Field(default=7, ge=1, le=365, description="Look back this many days from now.")] = 7,
        limit: Annotated[int, Field(default=50, ge=1, le=200, description="Maximum number of memories to return.")] = 50,
    ) -> str:
        """List memories captured in the last N days, newest first.

        Use this for time-scoped questions ('what did I work on this week')
        when a topical search is too narrow. Returns full memory metadata."""
        import time as _t
        now = _t.time()
        rows = app.store.get_memories_in_range(now - days * 86400.0, now, limit=limit)
        rows = sorted(rows, key=lambda r: float(r.get("created_at") or 0.0), reverse=True)
        return json.dumps(
            {"days": days, "count": len(rows),
             "results": [_memory_row_payload(r) for r in rows]},
            ensure_ascii=False,
        )

    @mcp.tool()
    @_guard
    def list_starred_memories(
        limit: Annotated[int, Field(default=20, ge=1, le=100, description="Maximum number of starred memories to return.")] = 20,
    ) -> str:
        """List memories the user explicitly starred — their curated, high-value
        items. Prefer these when deciding what the user themselves marked as
        important. Returns full memory metadata, newest first."""
        rows = app.store.get_starred(limit=limit)
        return json.dumps(
            {"count": len(rows), "results": [_memory_row_payload(r) for r in rows]},
            ensure_ascii=False,
        )

    @mcp.tool()
    @_guard
    def get_daily_digest(
        day: Annotated[str, Field(default="today", description="Which day: 'today', 'yesterday', or 'YYYY-MM-DD'.")] = "today",
    ) -> str:
        """Return the on-device daily digest (a short recap of the day's
        activity) for a day, if one has been generated. Read-only."""
        from datetime import date, timedelta
        d = (day or "today").strip().lower()
        if d == "today":
            key = date.today().isoformat()
        elif d == "yesterday":
            key = (date.today() - timedelta(days=1)).isoformat()
        else:
            key = day.strip()
        dig = app.store.get_digest(key)
        if not dig:
            return json.dumps(
                {"day": key, "digest": None, "note": "No digest for this day."},
                ensure_ascii=False,
            )
        return json.dumps(
            {"day": key, "digest": str(dig.get("content") or ""),
             "source_count": int(dig.get("source_count") or 0),
             "generated_at": float(dig.get("generated_at") or 0.0)},
            ensure_ascii=False,
        )

    @mcp.resource("corenous://stats")
    @_guard
    def stats() -> str:
        """Snapshot of the memory store: total count and latest capture time.

        Read this for context before deciding how to query."""
        store = app.store
        n = store.get_memory_count()
        recent = store.get_recent(limit=1)
        latest_ts = float(recent[0].get("created_at") or 0.0) if recent else 0.0
        try:
            vault_n = len(store.get_vault_entries())
        except Exception:
            vault_n = 0
        return json.dumps(
            {
                "memory_count": n,
                "vault_count": vault_n,
                "latest_capture_at": latest_ts,
                "latest_capture_iso": (
                    datetime.fromtimestamp(latest_ts, timezone.utc).isoformat()
                    if latest_ts else None
                ),
            },
            ensure_ascii=False,
        )

    return mcp


def _related_memories(app: AppContext, mid: int, limit: int = 5) -> list[dict[str, Any]]:
    """Semantic neighbours of ``mid`` via the stored compressed vectors.

    Reuses the memory's own cached vector as the query and scores it against
    every other cached vector — no embedding model needed. Mirrors the
    overlay's Related Memories logic: a 0.30 cosine floor and heading-level
    dedup so near-duplicate captures don't crowd the list."""
    cache = app.cache
    store = app.store
    if cache is None or store is None or len(cache) < 2:
        return []
    import numpy as np

    query_cv = None
    for cid, cv in cache.get_all():
        if int(cid) == int(mid):
            query_cv = cv
            break
    if query_cv is None:
        return []

    scores = cache.scores(query_cv)
    ids = cache.memory_ids()
    src_row = store.get_memory_by_id(int(mid)) or {}
    seen: set[str] = set()
    src_h = (src_row.get("heading") or "").strip().lower()
    if src_h:
        seen.add(src_h)

    out: list[dict[str, Any]] = []
    for i in np.argsort(-scores):
        sc = float(scores[int(i)])
        if sc < 0.30:
            break
        rid = int(ids[int(i)])
        if rid == int(mid):
            continue
        r = store.get_memory_by_id(rid)
        if not r or int(r.get("is_sensitive") or 0):
            continue
        h = (r.get("heading") or "").strip() or (r.get("text_snippet") or "").strip()[:60]
        if not h:
            continue
        hkey = h.lower()
        if hkey in seen:
            continue
        seen.add(hkey)
        out.append({
            "memory_id": rid,
            "heading": h,
            "created_at": float(r.get("created_at") or 0.0),
            "score": round(sc, 4),
        })
        if len(out) >= limit:
            break
    return out


def serve_stdio(app: AppContext) -> None:
    """Run the Corenous memory MCP server over stdio (blocking)."""
    build_server(app).run(transport="stdio")
