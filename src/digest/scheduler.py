"""Daily digest scheduling: polled trigger that fires the synthesiser
once per day after a configured local time, persists the result to the
daily_digests cache, and invokes a delivery callback (typically the
notification dispatcher in AppController).

The check is intentionally stateless beyond the SQLite cache. Each tick:

  1. Skip if a generation is already in flight.
  2. Skip if the current local hour is before the delivery hour.
  3. Skip if today's digest is already in the cache.
  4. Otherwise spawn a daemon thread that pulls today's memories, runs
     ai_daily_digest, persists via upsert_digest, and calls
     on_delivered(day_key, digest) so the caller can post a notification.

Running generation on a thread keeps the AppKit run loop responsive
while the LLM produces the digest (which can take 60 seconds or more on
local Llama 3.2 3B).
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..memory.store import MemoryStore


class DigestScheduler:
    """Daily digest delivery loop, called periodically from an AppKit timer."""

    def __init__(
        self,
        store: "MemoryStore",
        delivery_hour: int = 18,
        on_delivered: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._store = store
        self._delivery_hour = max(0, min(23, int(delivery_hour)))
        self._on_delivered = on_delivered
        self._generating = False
        self._generating_lock = threading.Lock()

    def check_and_deliver(self, *, now: Optional[datetime] = None) -> str:
        """Run one tick. Returns a short status string describing what
        happened (mostly for logging and tests): ``"skip:hour"``,
        ``"skip:cached"``, ``"skip:in-flight"``, or ``"started"``.
        """
        if not self._mark_started_if_idle():
            return "skip:in-flight"

        try:
            current = now or datetime.now()
            if current.hour < self._delivery_hour:
                return "skip:hour"

            day_key = current.strftime("%Y-%m-%d")
            cached = self._store.get_digest(day_key)
            if cached and (cached.get("content") or "").strip():
                return "skip:cached"

            threading.Thread(
                target=self._worker,
                args=(day_key, current),
                name="corenous-digest-gen",
                daemon=True,
            ).start()
            return "started"
        except Exception:
            # If we set the flag but failed before the worker started, clear
            # it so the next tick can try again.
            self._clear_generating()
            raise

    # ── internals ─────────────────────────────────────────────────────

    def _mark_started_if_idle(self) -> bool:
        """Atomic check-and-set so two simultaneous ticks cannot both
        spawn a worker."""
        with self._generating_lock:
            if self._generating:
                return False
            self._generating = True
            return True

    def _clear_generating(self) -> None:
        with self._generating_lock:
            self._generating = False

    def _worker(self, day_key: str, now: datetime) -> None:
        """Background thread. Pulls today's memories, calls
        ai_daily_digest, persists, fires the delivery callback."""
        try:
            start_ts = datetime(now.year, now.month, now.day).timestamp()
            end_ts = start_ts + 86400.0
            rows = self._store.get_memories_in_range(start_ts, end_ts, limit=500)
            if not rows:
                return
            from ..ai.llm import load_model_sync
            from ..ai.summarizer import ai_daily_digest
            if not load_model_sync(timeout=180):
                return
            digest = ai_daily_digest(rows, day_label="Today")
            digest = (digest or "").strip()
            if not digest:
                return
            self._store.upsert_digest(day_key, digest, time.time(), len(rows))
            if self._on_delivered is not None:
                try:
                    self._on_delivered(day_key, digest)
                except Exception:
                    if os.environ.get("CORENOUS_VERBOSE") == "1":
                        import traceback
                        traceback.print_exc()
        except Exception:
            if os.environ.get("CORENOUS_VERBOSE") == "1":
                import traceback
                traceback.print_exc()
        finally:
            self._clear_generating()
