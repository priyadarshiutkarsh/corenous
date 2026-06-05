"""Tests for temporal-proximity scoring in combined_search.

NOTE: this is verified with a targeted test, not the LoCoMo harness. LoCoMo
ingests every turn at the same wall-clock time, so its memories have no spread
in created_at and temporal scoring cannot move that benchmark. Real corenous
memories carry real timestamps, which is what this exercises.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.app.search_combo import combined_search, _query_time_window
from src.memory.store import MemoryStore
from src.memory.vector_cache import VectorCache
from src.turboquant import encoder as tq


_NOW = dt.datetime(2026, 6, 4, 12, 0, 0)   # fixed reference clock for the parser
_NOW_TS = _NOW.timestamp()


class TestQueryTimeWindow(unittest.TestCase):
    def test_today(self):
        s, e = _query_time_window("what did I read today", _NOW_TS)
        self.assertEqual(dt.datetime.fromtimestamp(s).date(), dt.date(2026, 6, 4))
        self.assertAlmostEqual(e - s, 86400.0, delta=3600)  # one day (DST-tolerant)

    def test_yesterday(self):
        s, _ = _query_time_window("the doc from yesterday", _NOW_TS)
        self.assertEqual(dt.datetime.fromtimestamp(s).date(), dt.date(2026, 6, 3))

    def test_weekday_is_most_recent_occurrence(self):
        s, e = _query_time_window("the paper from tuesday", _NOW_TS)
        d = dt.datetime.fromtimestamp(s).date()
        self.assertEqual(d.weekday(), 1)               # Tuesday
        self.assertLessEqual((_NOW.date() - d).days, 6)  # within the past week
        self.assertAlmostEqual(e - s, 86400.0, delta=3600)

    def test_month_in_date_context(self):
        s, e = _query_time_window("what I saw in may", _NOW_TS)
        self.assertEqual(dt.datetime.fromtimestamp(s).date(), dt.date(2026, 5, 1))
        self.assertEqual(dt.datetime.fromtimestamp(e).date(), dt.date(2026, 6, 1))

    def test_bare_month_word_not_matched(self):
        # "may"/"march" as ordinary words must not trigger a time window.
        self.assertIsNone(_query_time_window("may I open the file", _NOW_TS))
        self.assertIsNone(_query_time_window("march the team forward", _NOW_TS))

    def test_last_week_rolling(self):
        s, e = _query_time_window("stuff from last week", _NOW_TS)
        self.assertAlmostEqual(e, _NOW_TS, delta=1)
        self.assertAlmostEqual(_NOW_TS - s, 7 * 86400.0, delta=1)

    def test_no_time_reference(self):
        self.assertIsNone(_query_time_window("scaling laws paper", _NOW_TS))


def _new_store() -> MemoryStore:
    return MemoryStore(Path(tempfile.mkdtemp()) / "m.db")


def _unit(rng) -> np.ndarray:
    x = rng.standard_normal(384).astype(np.float32)
    return x / np.linalg.norm(x)


class _FakeEmbedder:
    def __init__(self, vec):
        self._vec = vec

    def embed(self, _text, is_query=False):
        return self._vec


class TestTemporalProximityBoost(unittest.TestCase):
    def test_memory_in_query_window_ranks_first(self):
        store = _new_store()
        v = _unit(np.random.default_rng(13))
        cv = tq.encode(v)
        blob = v.astype(np.float16).tobytes()
        # Two memories with the SAME vector (equal semantic score); only their
        # timestamps differ, so the temporal boost is the sole tiebreaker.
        a = store.insert_memory("alpha note on the project", "clipboard", "App",
                                cv, cv.residual_norm, fp16=blob)
        b = store.insert_memory("beta note on the project", "clipboard", "App",
                                cv, cv.residual_norm, fp16=blob)

        # Date 'a' to the Tuesday the parser will pick, 'b' to the day before it
        # (both several days old, so the recency bonus is ~0 for both).
        today = dt.date.today()
        tue = today - dt.timedelta(days=(today.weekday() - 1) % 7)
        a_ts = dt.datetime.combine(tue, dt.time(12, 0)).timestamp()
        b_ts = dt.datetime.combine(tue - dt.timedelta(days=1), dt.time(12, 0)).timestamp()
        store._conn.execute("UPDATE memories SET created_at=? WHERE id=?", (a_ts, a))
        store._conn.execute("UPDATE memories SET created_at=? WHERE id=?", (b_ts, b))
        store._conn.commit()

        cache = VectorCache(Path(tempfile.mkdtemp()) / "v.npy")
        cache.load_from_store(store.get_all_compressed_vectors())

        res = combined_search("the project note from tuesday", store, cache,
                              _FakeEmbedder(v), top_k=5)
        self.assertTrue(res)
        self.assertEqual(res[0].memory_id, a)


if __name__ == "__main__":
    unittest.main()
