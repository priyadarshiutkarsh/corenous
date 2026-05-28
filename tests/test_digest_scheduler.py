"""
Tests for DigestScheduler — phases 3+4 of the daily digest feature.

The scheduler is called periodically from an AppKit timer. Each tick
decides whether to generate today's digest, runs the generator on a
background thread, persists the result, and invokes a delivery
callback. These tests run the decision logic synchronously by patching
threading.Thread so the worker executes inline, and patch the AI
functions so no model is loaded.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.digest.scheduler import DigestScheduler


def _inline_thread_patch():
    """Make threading.Thread.start() run the target synchronously so the
    worker executes within the test instead of on a real OS thread."""
    real_thread = threading.Thread

    def _Sync(target=None, args=(), **_kwargs):
        t = real_thread(target=target, args=args)

        def _start():
            if target is not None:
                target(*args)
        t.start = _start
        return t

    return patch("src.digest.scheduler.threading.Thread", _Sync)


class TestCheckAndDeliverGuards(unittest.TestCase):

    def test_skip_when_before_delivery_hour(self):
        store = MagicMock()
        sched = DigestScheduler(store=store, delivery_hour=18)
        result = sched.check_and_deliver(now=datetime(2026, 5, 28, 9, 0))
        self.assertEqual(result, "skip:hour")
        store.get_digest.assert_not_called()
        store.get_memories_in_range.assert_not_called()

    def test_skip_when_already_cached_today(self):
        store = MagicMock()
        store.get_digest.return_value = {
            "day_key": "2026-05-28",
            "content": "Already generated for today.",
            "generated_at": time.time(),
            "source_count": 50,
        }
        sched = DigestScheduler(store=store, delivery_hour=18)
        result = sched.check_and_deliver(now=datetime(2026, 5, 28, 19, 0))
        self.assertEqual(result, "skip:cached")
        store.get_memories_in_range.assert_not_called()

    def test_skip_when_cached_content_is_empty_string(self):
        """An empty cached row must NOT count as 'already delivered' —
        the user should still get a digest today."""
        store = MagicMock()
        store.get_digest.return_value = {
            "day_key": "2026-05-28",
            "content": "   ",
            "generated_at": time.time(),
            "source_count": 0,
        }
        sched = DigestScheduler(store=store, delivery_hour=18)
        with _inline_thread_patch(), \
             patch("src.ai.llm.load_model_sync", return_value=True), \
             patch("src.ai.summarizer.ai_daily_digest", return_value="• new"):
            store.get_memories_in_range.return_value = [{"id": 1, "created_at": 0}]
            result = sched.check_and_deliver(now=datetime(2026, 5, 28, 19, 0))
        self.assertEqual(result, "started")

    def test_skip_when_generation_in_flight(self):
        """If a worker is already running, a second tick must not start
        a parallel worker — the cache write would race."""
        store = MagicMock()
        sched = DigestScheduler(store=store, delivery_hour=18)
        # Simulate a worker already running.
        sched._generating = True
        result = sched.check_and_deliver(now=datetime(2026, 5, 28, 19, 0))
        self.assertEqual(result, "skip:in-flight")
        store.get_digest.assert_not_called()

    def test_delivery_hour_is_clamped(self):
        sched_low = DigestScheduler(store=MagicMock(), delivery_hour=-5)
        sched_high = DigestScheduler(store=MagicMock(), delivery_hour=99)
        self.assertEqual(sched_low._delivery_hour, 0)
        self.assertEqual(sched_high._delivery_hour, 23)


class TestWorkerSuccessPath(unittest.TestCase):

    def test_worker_pulls_memories_runs_ai_and_persists(self):
        store = MagicMock()
        store.get_memories_in_range.return_value = [
            {"id": 1, "created_at": 1.0}, {"id": 2, "created_at": 2.0},
        ]
        callback = MagicMock()
        sched = DigestScheduler(
            store=store, delivery_hour=18, on_delivered=callback,
        )
        with patch("src.ai.llm.load_model_sync", return_value=True), \
             patch("src.ai.summarizer.ai_daily_digest", return_value="• synthesised"):
            sched._worker("2026-05-28", datetime(2026, 5, 28, 19, 0))
        store.upsert_digest.assert_called_once()
        args, _ = store.upsert_digest.call_args
        self.assertEqual(args[0], "2026-05-28")
        self.assertEqual(args[1], "• synthesised")
        self.assertEqual(args[3], 2)  # source_count
        callback.assert_called_once_with("2026-05-28", "• synthesised")

    def test_worker_skips_when_no_memories(self):
        """Empty days do not generate a digest. The cache stays empty and
        no callback fires."""
        store = MagicMock()
        store.get_memories_in_range.return_value = []
        callback = MagicMock()
        sched = DigestScheduler(
            store=store, delivery_hour=18, on_delivered=callback,
        )
        with patch("src.ai.llm.load_model_sync", return_value=True), \
             patch("src.ai.summarizer.ai_daily_digest", return_value="• synthesised"):
            sched._worker("2026-05-28", datetime(2026, 5, 28, 19, 0))
        store.upsert_digest.assert_not_called()
        callback.assert_not_called()

    def test_worker_skips_when_model_load_fails(self):
        store = MagicMock()
        store.get_memories_in_range.return_value = [{"id": 1, "created_at": 0}]
        sched = DigestScheduler(store=store, delivery_hour=18)
        with patch("src.ai.llm.load_model_sync", return_value=False), \
             patch("src.ai.summarizer.ai_daily_digest", return_value="x") as ai:
            sched._worker("2026-05-28", datetime(2026, 5, 28, 19, 0))
        ai.assert_not_called()
        store.upsert_digest.assert_not_called()

    def test_worker_skips_when_ai_returns_blank(self):
        store = MagicMock()
        store.get_memories_in_range.return_value = [{"id": 1, "created_at": 0}]
        callback = MagicMock()
        sched = DigestScheduler(
            store=store, delivery_hour=18, on_delivered=callback,
        )
        with patch("src.ai.llm.load_model_sync", return_value=True), \
             patch("src.ai.summarizer.ai_daily_digest", return_value="   "):
            sched._worker("2026-05-28", datetime(2026, 5, 28, 19, 0))
        store.upsert_digest.assert_not_called()
        callback.assert_not_called()

    def test_worker_always_clears_in_flight_flag(self):
        """Even if the AI raises, the _generating flag must clear so the
        next tick can retry."""
        store = MagicMock()
        store.get_memories_in_range.return_value = [{"id": 1, "created_at": 0}]
        sched = DigestScheduler(store=store, delivery_hour=18)
        sched._generating = True  # set as if we started
        with patch("src.ai.llm.load_model_sync", return_value=True), \
             patch("src.ai.summarizer.ai_daily_digest", side_effect=RuntimeError("boom")):
            sched._worker("2026-05-28", datetime(2026, 5, 28, 19, 0))
        self.assertFalse(sched._generating)

    def test_callback_exception_does_not_break_worker(self):
        store = MagicMock()
        store.get_memories_in_range.return_value = [{"id": 1, "created_at": 0}]
        sched = DigestScheduler(
            store=store, delivery_hour=18,
            on_delivered=MagicMock(side_effect=RuntimeError("bad subscriber")),
        )
        with patch("src.ai.llm.load_model_sync", return_value=True), \
             patch("src.ai.summarizer.ai_daily_digest", return_value="• ok"):
            sched._worker("2026-05-28", datetime(2026, 5, 28, 19, 0))
        # Persist still happened (this is the important side effect).
        store.upsert_digest.assert_called_once()


class TestQueryRange(unittest.TestCase):

    def test_worker_queries_local_midnight_to_midnight_window(self):
        """The query window must be exactly the calendar day (start of
        day to start of next day), independent of when the worker runs."""
        store = MagicMock()
        store.get_memories_in_range.return_value = []
        sched = DigestScheduler(store=store, delivery_hour=18)
        sched._worker("2026-05-28", datetime(2026, 5, 28, 19, 32))
        args, _ = store.get_memories_in_range.call_args
        start_ts, end_ts = args[0], args[1]
        expected_start = datetime(2026, 5, 28, 0, 0).timestamp()
        self.assertAlmostEqual(start_ts, expected_start, places=0)
        self.assertAlmostEqual(end_ts - start_ts, 86400.0, places=0)


if __name__ == "__main__":
    unittest.main()
