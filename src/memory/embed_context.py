"""Context envelope for embedding.

LoCoMo showed that embedding a capture in isolation costs a lot of retrieval
recall: a fragment like "yeah, that was last May" is unfindable on its own, but
trivially findable once the surrounding signal (where, when, what came just
before) rides along in the vector. This wraps the EMBEDDING INPUT with that
context. The stored and displayed memory text is unchanged; only the vector sees
the envelope, so the timeline and raw-capture views are untouched.

Junk context (empty fields, "Untitled", bare URLs, browser chrome) is dropped so
the envelope adds signal, not noise.
"""
from __future__ import annotations

import re
import time

_NOISE = re.compile(
    r"^(untitled|new tab|loading|about:blank|https?://|www\.)",
    re.IGNORECASE,
)


def _useful(line: str) -> bool:
    s = (line or "").strip()
    return bool(s) and _NOISE.match(s) is None


def build_embed_text(body: str, context: list[str]) -> str:
    """Prepend the useful ``context`` lines to ``body`` for embedding.

    ``context`` is ordered most-general to most-specific (e.g. window title, app,
    date, then the heading/snippet of the capture just before). Empty or noisy
    lines are dropped. If nothing useful remains, the body is returned unchanged.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for c in context:
        s = (c or "").strip()
        if _useful(s) and s.lower() not in seen:
            lines.append(s)
            seen.add(s.lower())
    body = (body or "").strip()
    parts = lines + ([body] if body else [])
    return "\n".join(parts)


def capture_embed_text(
    body: str,
    *,
    window_title: str = "",
    app_name: str = "",
    ts: float | None = None,
    prev_heading: str = "",
) -> str:
    """The production envelope for a capture's EMBEDDING input: window title,
    app, capture date, and the heading of the immediately preceding capture,
    most general first. The stored and displayed text is never changed; only
    the vector sees this."""
    date_line = time.strftime("%A %d %B %Y", time.localtime(ts)) if ts else ""
    return build_embed_text(body, [window_title, app_name, date_line, prev_heading])
