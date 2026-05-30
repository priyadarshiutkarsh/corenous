from __future__ import annotations

from dataclasses import dataclass


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
