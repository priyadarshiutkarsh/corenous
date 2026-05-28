"""
Tests for the routine notifier's non blocking dialog presentation.

Before this fix, _present called _show_dialog synchronously on the main
AppKit thread, and _show_dialog ran subprocess.run with a 5 minute
timeout. The whole AppKit run loop was blocked for up to 5 minutes
while the user decided whether to click Execute, Dismiss, or Snooze.

The fix spawns a background thread for the subprocess, then dispatches
the result handler back to the main queue so AppKit interactions
remain on the main thread.

These tests patch threading.Thread to run the worker synchronously and
patch the main queue dispatch to run inline, so the result handling is
testable without an AppKit run loop.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import after sys.path setup so the notifier module loads from src/
from src.routines.notifier import RoutineNotificationManager


# A minimal row that satisfies _present's row["..."] reads.
def _row(id: str = "rid-1", action_type: str = "open_app",
        action_data: str = "Slack") -> dict:
    return {
        "id": id,
        "title": "Open Slack",
        "description": "morning",
        "action_type": action_type,
        "action_data": action_data,
        "time_of_day_hour": 9.0,
        "days_seen": 4,
        "confidence": 0.7,
        "suggested_at": time.time(),
    }


def _make_manager(execute_callback=None) -> tuple[RoutineNotificationManager, MagicMock]:
    store = MagicMock()
    mgr = RoutineNotificationManager(store=store, execute_callback=execute_callback or MagicMock())
    return mgr, store


def _inline_dispatch_patch(mgr):
    """Make _dispatch_to_main run the block synchronously so the result
    handler executes within the test instead of waiting on a main run loop."""
    return patch.object(mgr, "_dispatch_to_main",
                        side_effect=lambda block: block())


def _inline_thread_patch():
    """Make threading.Thread.start() invoke target() synchronously so the
    worker runs before assertions, not on a real OS thread."""
    real_thread = threading.Thread

    def _Sync(target=None, **_kwargs):
        t = real_thread(target=target)

        def _start():
            if target is not None:
                target()
        t.start = _start
        return t

    return patch("src.routines.notifier.threading.Thread", _Sync)


class TestPresentRunsDialogOffMainThread(unittest.TestCase):

    def test_present_returns_immediately_without_blocking_on_dialog(self):
        """The fix: _present must return before _show_dialog finishes.
        We simulate _show_dialog as a slow call and verify _present
        returns while the dialog is still 'open'."""
        mgr, store = _make_manager()
        dialog_started = threading.Event()
        dialog_done = threading.Event()

        def _slow_dialog(routine):
            dialog_started.set()
            # Wait for test to release us.
            dialog_done.wait(timeout=2)
            return "dismiss"

        with patch.object(mgr, "_show_dialog", side_effect=_slow_dialog), \
             patch.object(mgr, "_dispatch_to_main", side_effect=lambda b: b()):
            start = time.time()
            mgr._present(_row())
            returned_at = time.time()
            # The dialog thread is still running (we have not signalled done).
            self.assertTrue(dialog_started.wait(timeout=1))
            # _present must have returned essentially immediately, well
            # under the simulated dialog time.
            self.assertLess(returned_at - start, 0.5,
                            "_present should not block on the dialog")
            dialog_done.set()  # let the thread exit cleanly


class TestDialogResultRouting(unittest.TestCase):

    def test_execute_choice_fires_callback_and_marks_executed(self):
        cb = MagicMock()
        mgr, store = _make_manager(execute_callback=cb)
        with _inline_thread_patch(), \
             _inline_dispatch_patch(mgr), \
             patch.object(mgr, "_show_dialog", return_value="execute"):
            mgr._present(_row(id="r1"))
        cb.assert_called_once()
        store.mark_routine_executed.assert_called_once_with("r1")
        store.mark_routine_dismissed.assert_not_called()
        store.mark_routine_pending.assert_not_called()

    def test_dismiss_choice_marks_dismissed(self):
        cb = MagicMock()
        mgr, store = _make_manager(execute_callback=cb)
        with _inline_thread_patch(), \
             _inline_dispatch_patch(mgr), \
             patch.object(mgr, "_show_dialog", return_value="dismiss"):
            mgr._present(_row(id="r2"))
        cb.assert_not_called()
        store.mark_routine_dismissed.assert_called_once_with("r2")

    def test_snooze_choice_marks_pending(self):
        mgr, store = _make_manager()
        with _inline_thread_patch(), \
             _inline_dispatch_patch(mgr), \
             patch.object(mgr, "_show_dialog", return_value="snooze"):
            mgr._present(_row(id="r3"))
        store.mark_routine_pending.assert_called_once_with("r3")

    def test_dialog_exception_falls_through_to_snooze(self):
        """If the subprocess call raises, the user's routine should
        revert to pending so it can re-prompt later, not crash the app."""
        mgr, store = _make_manager()
        with _inline_thread_patch(), \
             _inline_dispatch_patch(mgr), \
             patch.object(mgr, "_show_dialog", side_effect=RuntimeError("osascript missing")):
            mgr._present(_row(id="r4"))
        store.mark_routine_pending.assert_called_once_with("r4")


class TestPresentingFlag(unittest.TestCase):

    def test_present_sets_presenting_flag_immediately(self):
        """check_and_notify uses _presenting to avoid stacking dialogs.
        The flag must be set BEFORE the background thread starts."""
        mgr, store = _make_manager()
        dialog_started = threading.Event()
        dialog_done = threading.Event()

        def _slow_dialog(routine):
            dialog_started.set()
            dialog_done.wait(timeout=2)
            return "dismiss"

        with patch.object(mgr, "_show_dialog", side_effect=_slow_dialog), \
             patch.object(mgr, "_dispatch_to_main", side_effect=lambda b: b()):
            self.assertFalse(mgr._presenting)
            mgr._present(_row())
            # After _present returns, the worker is still mid dialog.
            self.assertTrue(mgr._presenting,
                            "flag must remain True while dialog is open")
            dialog_done.set()

    def test_presenting_flag_clears_after_result_handled(self):
        mgr, store = _make_manager()
        with _inline_thread_patch(), \
             _inline_dispatch_patch(mgr), \
             patch.object(mgr, "_show_dialog", return_value="dismiss"):
            mgr._present(_row())
        self.assertFalse(mgr._presenting,
                         "flag must clear after result is handled")

    def test_presenting_clears_even_when_dialog_raises(self):
        mgr, store = _make_manager()
        with _inline_thread_patch(), \
             _inline_dispatch_patch(mgr), \
             patch.object(mgr, "_show_dialog", side_effect=RuntimeError("boom")):
            mgr._present(_row())
        self.assertFalse(mgr._presenting,
                         "flag must clear in the finally branch")


class TestDispatchToMain(unittest.TestCase):

    def test_dispatch_falls_through_to_inline_when_foundation_unavailable(self):
        """Tests run without a running AppKit loop, so the fallback path
        is what actually executes. Verify it does invoke the block."""
        mgr, _ = _make_manager()
        # Force the import inside _dispatch_to_main to fail by patching
        # NSOperationQueue at the module path it imports from.
        ran = []
        with patch("Foundation.NSOperationQueue") as mock_q:
            mock_q.mainQueue.side_effect = RuntimeError("no run loop")
            mgr._dispatch_to_main(lambda: ran.append(True))
        self.assertEqual(ran, [True])


if __name__ == "__main__":
    unittest.main()
