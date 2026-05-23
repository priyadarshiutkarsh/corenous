"""
Hybrid search: TurboQuant cosine + FTS5 BM25, fused with Reciprocal Rank
Fusion (RRF). Then layered bonuses for exact-match in heading, recency, and
metadata. RRF is the same fusion strategy used by Elastic and Vespa for
hybrid lexical + dense search; it does not require either ranker's scores
to be on the same scale.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from ..memory.search import SearchResult
from ..memory.store import MemoryStore
from ..memory.vector_cache import VectorCache
from ..memory.embedder import Embedder
from ..turboquant import encoder as tq

# RRF constant; 60 is the value used in the original Cormack et al. paper.
_RRF_K = 60.0


_BROWSER_APPS = frozenset({
    "chrome", "safari", "firefox", "brave", "arc", "edge", "browser",
})
_CODE_APPS = frozenset({
    "code", "cursor", "xcode", "pycharm", "webstorm", "intellij", "vscode",
})
_CHAT_APPS = frozenset({
    "slack", "discord", "telegram", "messages", "whatsapp", "signal",
})


def _expand_query(query: str) -> str:
    """Add synonyms/related terms that improve recall for common query patterns."""
    q = query.lower()
    extras: list[str] = []
    if any(w in q for w in ("web", "browse", "browsed", "browsing", "internet", "online", "site")):
        extras.extend(["browser", "browsed", "website"])
    if any(w in q for w in ("code", "coding", "program", "dev")):
        extras.extend(["code editor", "vscode", "cursor"])
    if any(w in q for w in ("chat", "message", "dm", "conversation")):
        extras.extend(["slack", "discord", "messages"])
    if any(w in q for w in ("search", "searched", "google")):
        extras.append("searched web")
    if extras:
        return query + " " + " ".join(extras)
    return query


def combined_search(
    query: str,
    store: MemoryStore,
    cache: VectorCache,
    embedder: Embedder,
    top_k: int = 12,
) -> list[SearchResult]:
    """Return top_k results blending semantic similarity, FTS5, and recency."""
    query = query.strip()

    # Empty query → empty Search tab. Timeline owns browsing/recent history.
    if not query:
        return []

    expanded = _expand_query(query)

    scores: dict[int, float] = {}
    meta:   dict[int, dict]  = {}
    # Per-ranker rank lookups so we can debug / tune the fusion later.
    vec_rank: dict[int, int] = {}
    fts_rank: dict[int, int] = {}

    # ── Dense ranker (TurboQuant cosine) ──────────────────────────────────────
    if len(cache) > 0:
        vec = embedder.embed(expanded)
        query_cv = tq.encode(vec)
        memory_ids  = cache.memory_ids()
        raw_scores = cache.scores(query_cv)
        candidate_n = min(len(raw_scores), max(top_k * 20, 200))
        if candidate_n < len(raw_scores):
            candidate_idx = np.argpartition(raw_scores, -candidate_n)[-candidate_n:]
            candidate_idx = candidate_idx[np.argsort(raw_scores[candidate_idx])[::-1]]
        else:
            candidate_idx = np.argsort(raw_scores)[::-1]
        for rank, idx in enumerate(candidate_idx):
            mid = int(memory_ids[int(idx)])
            vec_rank[mid] = rank
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (_RRF_K + rank)

    # ── Lexical ranker (FTS5 BM25) ────────────────────────────────────────────
    fts_rows = store.fts_search(query, limit=max(top_k * 4, 60))
    for rank, row in enumerate(fts_rows):
        mid = int(row["id"])
        fts_rank[mid] = rank
        scores[mid] = scores.get(mid, 0.0) + 1.0 / (_RRF_K + rank)
        meta[mid] = row

    # ── Metadata fallback: catches short queries, app names, window titles ───
    # that FTS may miss because they're too short / too punctuated to tokenize
    # cleanly. We treat it as a third (weaker) ranker fed into RRF.
    meta_rows = store.metadata_search(query, limit=top_k * 3)
    for rank, row in enumerate(meta_rows):
        mid = int(row["id"])
        # Half-weight ranker (it's a fuzzier signal).
        scores[mid] = scores.get(mid, 0.0) + 0.5 / (_RRF_K + rank)
        meta.setdefault(mid, row)

    # Hydrate metadata for vector-only hits in one shot.
    missing = [mid for mid in scores if mid not in meta]
    if missing:
        for row in store.get_many_by_ids(missing):
            meta[int(row["id"])] = row

    # ── Layered bonuses on top of the RRF base ───────────────────────────────
    # (These are additive and small; they nudge order, they don't dominate.)
    now = time.time()
    q_low = query.lower()
    q_tokens_short = [t for t in q_low.split() if len(t) > 1]
    q_tokens_long  = [t for t in q_low.split() if len(t) > 2]

    for mid, row in list(meta.items()):
        # 1) Recency: up to +0.012 (tuned to match RRF magnitudes ~0.016).
        try:
            age_h = (now - float(row["created_at"])) / 3600.0
        except Exception:
            age_h = 0.0
        scores[mid] += max(0.0, 0.012 - age_h * 0.0012)

        # 2) Exact-match bonus on heading + summary.
        title_hay = (
            (row.get("heading") or "").lower()
            + " " + (row.get("summary") or "").lower()
        )
        if q_tokens_short and all(t in title_hay for t in q_tokens_short):
            scores[mid] += 0.020

        # 3) Heading / activity domain match.
        heading  = (row.get("heading") or "").lower()
        activity = (row.get("activity") or "").lower()
        if q_tokens_long and any(
            t in heading or t in activity for t in q_tokens_long
        ):
            scores[mid] += 0.008

        # 4) Starred memories get a tiny stickiness bump so explicit user
        #    annotations float over otherwise-tied results.
        if int(row.get("is_starred", 0) or 0):
            scores[mid] += 0.005

    # ── Hard filters: drop sensitive (defense in depth) and tombstoned ───────
    for mid in list(scores.keys()):
        row = meta.get(mid)
        if row is None:
            del scores[mid]
            continue
        if int(row.get("is_sensitive") or 0):
            del scores[mid]

    # ── Minimum relevance threshold ───────────────────────────────────────────
    if scores:
        max_score = max(scores.values())
        # Threshold scales with the top score; for tiny corpora we keep it low.
        min_threshold = max(0.0030, max_score * 0.10)
        scores = {mid: s for mid, s in scores.items() if s >= min_threshold}

    # ── Assemble results ──────────────────────────────────────────────────────
    sorted_ids = sorted(scores, key=lambda m: scores[m], reverse=True)[:top_k]
    results = []
    for mid in sorted_ids:
        row = meta.get(mid) or store.get_memory_by_id(mid)
        if row is None:
            continue
        results.append(SearchResult(
            memory_id=mid,
            score=round(scores[mid], 4),
            text_snippet=row["text_snippet"],
            source=row["source"],
            app_name=row["app_name"],
            created_at=float(row["created_at"]),
            tags=row.get("tags", ""),
            full_text=row.get("full_text", ""),
            is_starred=bool(row.get("is_starred", 0)),
            window_title=row.get("window_title", ""),
            bundle_id=row.get("bundle_id", ""),
            activity=row.get("activity", ""),
            heading=row.get("heading", ""),
            summary=row.get("summary", ""),
        ))
    return results
