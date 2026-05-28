"""
macOS notification delivery for suggested routines.

Surfaces one pending routine at a time as a modal NSAlert with **Execute** and
**Dismiss** buttons. We deliberately use NSAlert (not NSUserNotification) so
the prompt shows from any launch path — a bundled ``Corenous.app`` *and* a
plain ``corenous-ai start`` from source. NSUserNotification silently drops on
unbundled Python because the process has no registered notification ID.

Flow
----
1. ``app_controller`` calls ``RoutineNotificationManager(store, callback)`` on
   launch and schedules ``check_and_notify()`` on a timer.
2. ``check_and_notify()`` finds the highest-confidence pending routine whose
   typical hour is within ±2 h of *now*, marks it as notified in SQLite, and
   shows a non-blocking NSAlert sheet on the main thread.
3. Click **Execute** → fires ``execute_callback(routine)`` and the action
   handler in ``AppController`` performs ``open_app`` / ``open_url`` / etc.
4. Click **Dismiss** → routine is marked ``dismissed`` so it won't reappear
   today.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable, Optional, TYPE_CHECKING

import AppKit
import objc
from Foundation import NSObject

if TYPE_CHECKING:
    from ..memory.store import MemoryStore
    from .detector import SuggestedRoutine


# ---------------------------------------------------------------------------
# Public manager
# ---------------------------------------------------------------------------

class RoutineNotificationManager:
    """
    Owned by AppController. Call ``check_and_notify()`` periodically to
    surface pending routines whose typical hour is close to now.
    """

    def __init__(
        self,
        store: "MemoryStore",
        execute_callback: Callable,
    ) -> None:
        self._store = store
        self._on_execute = execute_callback
        self._presenting = False  # guard so timer ticks don't stack dialogs

    def check_and_notify(self) -> None:
        """Pick the best pending routine for the current hour and prompt."""
        if self._presenting:
            return
        try:
            pending = self._store.get_pending_routines()
        except Exception:
            return
        if not pending:
            return

        now_hour = datetime.now().hour + datetime.now().minute / 60.0
        chosen: Optional[dict] = None
        for row in pending:
            toh = float(row.get("time_of_day_hour", 12))
            if abs(now_hour - toh) > 2.0:
                continue
            chosen = row
            break
        if chosen is None:
            return
        self._present(chosen)

    # ── Internals ─────────────────────────────────────────────────────────

    def _present(self, row: dict) -> None:
        from .detector import SuggestedRoutine

        routine = SuggestedRoutine(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            action_type=row["action_type"],
            action_data=row["action_data"],
            time_of_day_hour=float(row.get("time_of_day_hour", 12)),
            days_seen=int(row.get("days_seen", 0)),
            confidence=float(row.get("confidence", 0)),
            suggested_at=float(row.get("suggested_at", time.time())),
        )

        # Mark as notified up-front so the same routine doesn't re-prompt
        # if the dialog is dismissed without choosing a button (e.g. window
        # closed via ⌘W) and the timer re-fires.
        try:
            self._store.mark_routine_notified(routine.id)
        except Exception:
            pass

        # _show_dialog blocks for up to 5 minutes waiting for the user to
        # click a button on the osascript modal. Running it on the main
        # AppKit thread (where this method is called from the NSTimer
        # callback) would freeze the menu bar, overlay, and run loop for
        # that whole window. Spawn a daemon thread for the subprocess and
        # dispatch the result back to the main queue so all AppKit
        # interactions (the execute callback, store writes) still happen
        # on the main thread.
        self._presenting = True

        def _worker() -> None:
            try:
                choice = self._show_dialog(routine)
            except Exception:
                choice = "snooze"
            self._dispatch_to_main(lambda: self._handle_dialog_result(routine, choice))

        threading.Thread(
            target=_worker,
            name="corenous-routine-dialog",
            daemon=True,
        ).start()

    def _dispatch_to_main(self, block) -> None:
        """Schedule ``block`` to run on the main AppKit thread.

        Uses NSOperationQueue's main queue so the call returns immediately
        from the background thread and the block runs when the main run
        loop services it. Falls back to synchronous execution only when
        Foundation is unavailable (which should never happen on macOS but
        keeps tests independent of AppKit).
        """
        try:
            from Foundation import NSOperationQueue
            NSOperationQueue.mainQueue().addOperationWithBlock_(block)
        except Exception:
            # Last resort: run inline so result handling still happens.
            # In tests this path is the one exercised because Foundation
            # may be patched out.
            try:
                block()
            except Exception:
                pass

    def _handle_dialog_result(self, routine, choice: str) -> None:
        """Process the user's dialog choice. Runs on the main thread.

        Side effects (execute callback, store mutations) all happen here,
        so any AppKit work in the callback is safe.
        """
        try:
            if choice == "execute":
                if self._on_execute is not None:
                    try:
                        self._on_execute(routine)
                    except Exception:
                        pass
                try:
                    self._store.mark_routine_executed(routine.id)
                except Exception:
                    pass
            elif choice == "dismiss":
                try:
                    self._store.mark_routine_dismissed(routine.id)
                except Exception:
                    pass
            else:
                # Snooze (or any other path) — revert to pending.
                try:
                    self._store.mark_routine_pending(routine.id)
                except Exception:
                    pass
        finally:
            self._presenting = False

    def _show_dialog(self, routine) -> str:
        """Show the routine prompt via ``osascript``.

        Why osascript and not NSAlert?
        ──────────────────────────────
        Corenous launches with ``NSApplicationActivationPolicyAccessory`` so
        it has no Dock icon, which means ``NSApp.activateIgnoringOtherApps_``
        cannot reliably bring an NSAlert to the front — the dialog opens
        underneath whatever app the user is currently in and is effectively
        invisible.

        ``osascript -e 'display dialog …'`` runs inside the System Events /
        AppleScript context, which always presents on top of the active app,
        regardless of the caller's activation policy. We pay a tiny fork()
        cost per dialog (~50 ms) for a guarantee that the user actually sees
        the prompt. Click results map back via the subprocess exit code and
        stdout content.

        Returns one of: ``"execute"``, ``"dismiss"``, ``"snooze"``.
        """
        import subprocess

        # AppleScript string escaping: backslashes and double quotes.
        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace('"', '\\"')

        title = esc(f"Corenous: {routine.title}")
        body = esc(routine.description)
        script = (
            f'display dialog "{body}" '
            f'with title "{title}" '
            f'buttons {{"Dismiss", "Snooze", "Execute"}} '
            f'default button "Execute" '
            f'cancel button "Dismiss" '
            f'with icon note'
        )

        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=300,  # 5 min — user has time to decide
            )
        except subprocess.TimeoutExpired:
            return "snooze"
        except Exception:
            return "snooze"

        # osascript returns exit code 1 when the cancel button is clicked.
        if result.returncode != 0:
            return "dismiss"

        stdout = (result.stdout or "").strip().lower()
        if "execute" in stdout:
            return "execute"
        if "snooze" in stdout:
            return "snooze"
        if "dismiss" in stdout:
            return "dismiss"
        return "snooze"
