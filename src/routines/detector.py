"""
Detect repetitive daily routines from the memory store.

Algorithm
---------
1. Pull non-sensitive memories from the last N days (default 14).
2. Group by calendar day, then by app_name and hour bucket.
3. Any app / URL domain that appears on >= min_days distinct days *at a
   consistent time of day* (>= 60 % of occurrences within ±2.5 h of
   the rolling average) becomes a SuggestedRoutine.
4. Results are ranked by confidence and capped at 5 suggestions.
"""
from __future__ import annotations

import hashlib
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..memory.store import MemoryStore


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SuggestedRoutine:
    id: str                 # stable content hash used as DB primary key
    title: str              # "Open Slack"
    description: str        # "You do this most mornings around 9 am"
    action_type: str        # open_app | open_url
    action_data: str        # app name or full URL
    time_of_day_hour: float # 0–23 float
    days_seen: int
    confidence: float       # 0–1
    suggested_at: float = field(default_factory=time.time)

    @staticmethod
    def make_id(action_type: str, action_data: str, hour: int) -> str:
        raw = f"{action_type}:{action_data.lower()}:{hour}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def detect_routines(
    store: "MemoryStore",
    min_days: int = 3,
    lookback_days: int = 14,
) -> list[SuggestedRoutine]:
    """Return up to 5 suggested routines sorted by confidence (highest first)."""
    since = time.time() - lookback_days * 86_400
    memories = _fetch(store, since)
    if not memories:
        return []

    app_routines = _detect_app_routines(memories, min_days)
    url_routines = _detect_url_routines(memories, min_days)

    seen_keys: set[tuple] = set()
    unique: list[SuggestedRoutine] = []
    for r in sorted(app_routines + url_routines,
                    key=lambda r: r.confidence, reverse=True):
        key = (r.action_type, r.action_data.lower())
        if key not in seen_keys:
            seen_keys.add(key)
            unique.append(r)

    return unique[:5]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

_IGNORE_APPS = frozenset({
    "", "finder", "dock", "loginwindow", "systempreferencesapp",
    "system preferences", "system settings", "spotlight",
    "notification center", "control center", "screensaverengine",
    "screencaptureui", "corenous",
})

_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?([a-z0-9][-a-z0-9.]+\.[a-z]{2,})", re.I
)

_NOISE_DOMAINS = frozenset({
    "apple.com", "icloud.com", "localhost", "google.com",
    "gstatic.com", "googleapis.com", "127.0.0.1",
})


def _fetch(store: "MemoryStore", since: float) -> list[dict]:
    try:
        rows = store._conn.execute(
            """
            SELECT app_name, activity, window_title, created_at, source
            FROM memories
            WHERE created_at >= ? AND is_sensitive = 0
            ORDER BY created_at ASC
            """,
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _detect_app_routines(
    memories: list[dict], min_days: int
) -> list[SuggestedRoutine]:
    """Find apps opened consistently at the same time of day."""
    # app -> [(date, fractional_hour)]
    buckets: dict[str, list[tuple]] = defaultdict(list)
    for m in memories:
        app = (m.get("app_name") or "").strip()
        if app.lower() in _IGNORE_APPS:
            continue
        dt = datetime.fromtimestamp(m["created_at"])
        buckets[app].append((dt.date(), dt.hour + dt.minute / 60.0))

    routines: list[SuggestedRoutine] = []
    for app, occ in buckets.items():
        distinct_days = {d for d, _ in occ}
        if len(distinct_days) < min_days:
            continue

        hours = [h for _, h in occ]
        avg_h = sum(hours) / len(hours)
        consistent = sum(1 for h in hours if abs(h - avg_h) <= 2.5) / len(hours)
        if consistent < 0.60:
            continue

        hour_int = int(avg_h)
        routines.append(SuggestedRoutine(
            id=SuggestedRoutine.make_id("open_app", app, hour_int),
            title=f"Open {app}",
            description=(
                f"You open {app} most {_time_label(avg_h)}s "
                f"({len(distinct_days)} days in a row). Open it now?"
            ),
            action_type="open_app",
            action_data=app,
            time_of_day_hour=avg_h,
            days_seen=len(distinct_days),
            confidence=round(consistent * min(1.0, len(distinct_days) / 7.0), 3),
        ))

    return routines


def _detect_url_routines(
    memories: list[dict], min_days: int
) -> list[SuggestedRoutine]:
    """Find domains visited consistently at the same time of day."""
    buckets: dict[str, list[tuple]] = defaultdict(list)
    for m in memories:
        # Deduplicate domains within a single memory: activity and window_title
        # often both contain the same domain, so iterating both fields without
        # deduplication would count one visit twice and inflate confidence.
        domains = {
            d
            for field_val in (m.get("activity") or "", m.get("window_title") or "")
            if (d := _extract_domain(field_val))
        }
        if not domains:
            continue
        dt = datetime.fromtimestamp(m["created_at"])
        entry = (dt.date(), dt.hour + dt.minute / 60.0)
        for domain in domains:
            buckets[domain].append(entry)

    routines: list[SuggestedRoutine] = []
    for domain, occ in buckets.items():
        distinct_days = {d for d, _ in occ}
        if len(distinct_days) < min_days:
            continue

        hours = [h for _, h in occ]
        avg_h = sum(hours) / len(hours)
        consistent = sum(1 for h in hours if abs(h - avg_h) <= 2.5) / len(hours)
        if consistent < 0.55:
            continue

        hour_int = int(avg_h)
        url = f"https://{domain}"
        routines.append(SuggestedRoutine(
            id=SuggestedRoutine.make_id("open_url", url, hour_int),
            title=f"Visit {domain}",
            description=(
                f"You visit {domain} most {_time_label(avg_h)}s "
                f"({len(distinct_days)} days in a row). Open it now?"
            ),
            action_type="open_url",
            action_data=url,
            time_of_day_hour=avg_h,
            days_seen=len(distinct_days),
            confidence=round(consistent * min(1.0, len(distinct_days) / 7.0), 3),
        ))

    return routines


def _extract_domain(text: str) -> str:
    m = _URL_RE.search(text)
    if not m:
        return ""
    domain = m.group(1).lower()
    if len(domain) < 5 or "." not in domain:
        return ""
    if any(n in domain for n in _NOISE_DOMAINS):
        return ""
    return domain


def _time_label(hour: float) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"
