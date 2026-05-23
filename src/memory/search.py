from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..turboquant import encoder as tq_encoder
from .vector_cache import VectorCache
from .store import MemoryStore


@dataclass
class SearchResult:
    memory_id: int
    score: float
    text_snippet: str
    source: str
    app_name: str
    created_at: float
    tags: str = ""
    full_text: str = ""
    is_starred: bool = False
    window_title: str = ""
    bundle_id: str = ""
    activity: str = ""
    heading: str = ""
    summary: str = ""


def search(
    query_vector: np.ndarray,
    store: MemoryStore,
    cache: VectorCache,
    top_k: int = 10,
    min_score: float = 0.30,
) -> list[SearchResult]:
    if len(cache) == 0:
        return []

    query_cv = tq_encoder.encode(query_vector)
    memory_ids = cache.memory_ids()
    scores = cache.scores(query_cv)

    # Sort descending, apply min_score threshold
    order = np.argsort(scores)[::-1]
    results = []
    for idx in order:
        if float(scores[idx]) < min_score:
            break
        if len(results) >= top_k:
            break
        mid = memory_ids[idx]
        row = store.get_memory_by_id(mid)
        if row is None:
            continue
        if int(row.get("is_sensitive") or 0):
            continue
        results.append(SearchResult(
            memory_id=mid,
            score=float(scores[idx]),
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
