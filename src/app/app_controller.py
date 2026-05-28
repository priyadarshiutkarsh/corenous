"""
Main application controller.
Sets up NSStatusItem (menu bar), SearchOverlay, and global ⌥⌘⇧Space hotkey.
Must be bootstrapped from the main thread (AppHelper.runEventLoop).
"""
from __future__ import annotations

import objc
import AppKit
from PyObjCTools import AppHelper
from pathlib import Path

from .overlay import SearchOverlay
from .search_engine import combined_search


# ── Menu action target ────────────────────────────────────────────────────────

class _MenuTarget(AppKit.NSObject):
    """Handles status icon clicks and right-click context menu."""

    _app = objc.ivar()

    def initWithApp_(self, app):
        self = objc.super(_MenuTarget, self).init()
        if self is None:
            return None
        self._app = app
        return self

    @objc.typedSelector(b"v@:@")
    def handleClick_(self, sender):
        """Left-click → toggle overlay. Ctrl/right-click → minimal context menu."""
        event = AppKit.NSApp.currentEvent()
        is_right = False
        if event is not None:
            is_right = (
                event.type() == AppKit.NSEventTypeRightMouseDown
                or bool(event.modifierFlags() & AppKit.NSEventModifierFlagControl)
            )
        if is_right:
            self._show_context_menu(sender, event)
        else:
            self._app.overlay.toggle()

    def _show_context_menu(self, sender, event):
        self._app._refresh_status_button_glyph()
        store = self._app._store
        n = store.get_memory_count() if store else 0
        v = len(store.get_vault_entries()) if store else 0
        paused = (store.get_config("capture_paused", "0") == "1") if store else False
        lite = (store.get_config("lite_mode", "0") == "1") if store else False

        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        info = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"{n} memories  ·  {v} encrypted", None, "")
        info.setEnabled_(False)
        menu.addItem_(info)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        # Pause / Resume capture toggle. The daemon checks this config flag
        # on every capture cycle and short-circuits without touching OCR,
        # AI, or embedding when paused — the single fastest way to make
        # the Mac calm down during a video call or focus block.
        pause_title = "Resume Capture" if paused else "Pause Capture"
        pause_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            pause_title, b"togglePause:", "p")
        pause_item.setTarget_(self)
        pause_item.setEnabled_(True)
        menu.addItem_(pause_item)

        lite_title = "Disable Lite Mode" if lite else "Enable Lite Mode"
        lite_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            lite_title, b"toggleLiteMode:", "l")
        lite_item.setTarget_(self)
        lite_item.setEnabled_(True)
        menu.addItem_(lite_item)

        # Replay the onboarding tour for users who want a refresher.
        tour_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Show Shortcut Tour", b"showTour:", "")
        tour_item.setTarget_(self)
        tour_item.setEnabled_(True)
        menu.addItem_(tour_item)

        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        # Determine daemon liveness so the menu offers the right action.
        import os as _os
        pid_file = self._app._data_dir / "daemon.pid"
        daemon_alive = False
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                _os.kill(pid, 0)
                daemon_alive = True
            except (ValueError, ProcessLookupError):
                daemon_alive = False
            except PermissionError:
                daemon_alive = True
        capture_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Stop Corenous" if daemon_alive else "Start Corenous",
            b"toggleDaemon:", "")
        capture_item.setTarget_(self)
        capture_item.setEnabled_(True)
        menu.addItem_(capture_item)

        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Corenous AI", b"quitApp:", "q")
        quit_item.setTarget_(self)
        quit_item.setEnabled_(True)
        menu.addItem_(quit_item)

        if event:
            AppKit.NSMenu.popUpContextMenu_withEvent_forView_(menu, event, sender)

    @objc.typedSelector(b"v@:@")
    def togglePause_(self, sender):
        store = self._app._store
        if not store:
            return
        cur = store.get_config("capture_paused", "0")
        new = "0" if cur == "1" else "1"
        store.set_config("capture_paused", new)
        self._app._refresh_status_button_glyph()
        if self._app.overlay is not None:
            try:
                self._app.overlay._on_capture_pause_changed(new == "1")
            except Exception:
                pass

    @objc.typedSelector(b"v@:@")
    def toggleLiteMode_(self, sender):
        store = self._app._store
        if not store:
            return
        cur = store.get_config("lite_mode", "0")
        new = "0" if cur == "1" else "1"
        store.set_config("lite_mode", new)
        self._app._refresh_status_button_glyph()
        if self._app.overlay is not None:
            try:
                self._app.overlay._on_lite_mode_changed(new == "1")
            except Exception:
                pass

    @objc.typedSelector(b"v@:@")
    def showSearch_(self, sender):
        self._app.overlay.show()

    @objc.typedSelector(b"v@:@")
    def showTour_(self, sender):
        """Open the overlay (if needed) and show the onboarding tour."""
        if self._app.overlay is None:
            return
        try:
            self._app.overlay.show()
            self._app.overlay.show_onboarding()
        except Exception:
            pass

    @objc.typedSelector(b"v@:@")
    def toggleDaemon_(self, sender):
        """Stop or start the background capture daemon from the menu bar.

        Mirrors what ``corenous-ai daemon start/stop`` does but accessible
        from inside the bundle without a terminal. Stopping sends SIGTERM
        to the pid in ``daemon.pid``; starting calls back into the
        controller's spawn helper so paths stay consistent."""
        import os
        import signal as _sig
        pid_file = self._app._data_dir / "daemon.pid"
        alive = False
        pid = None
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)
                alive = True
            except (ValueError, ProcessLookupError):
                pid = None
            except PermissionError:
                alive = True
        if alive and pid is not None:
            try:
                os.kill(pid, _sig.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                pid_file.unlink()
            except FileNotFoundError:
                pass
            if self._app.overlay is not None:
                try:
                    self._app.overlay._flash_status("Background capture stopped")
                except Exception:
                    pass
            return
        # Otherwise: spawn it fresh.
        try:
            self._app._spawn_daemon_if_needed()
            if self._app.overlay is not None:
                try:
                    self._app.overlay._flash_status("Background capture starting")
                except Exception:
                    pass
        except Exception:
            pass

    @objc.typedSelector(b"v@:@")
    def quitApp_(self, sender):
        AppKit.NSApp.terminate_(None)


# ── App controller ────────────────────────────────────────────────────────────

class AppController(AppKit.NSObject):

    def initWithDataDir_configPath_(self, data_dir: Path, config_path: Path):
        self = objc.super(AppController, self).init()
        if self is None:
            return None
        self._data_dir        = data_dir
        self._config_path     = config_path
        self._store           = None
        self._cache           = None
        self._embedder        = None
        self._status_item     = None
        self._menu_target     = None
        self._mark_image      = None
        self._hotkey_monitor  = None
        self.overlay          = None
        self._routine_manager = None  # RoutineNotificationManager
        self._routine_timer   = None
        return self

    def applicationDidFinishLaunching_(self, notification):
        # Menu-bar accessory: no Dock entry. Re-assert the policy as a
        # defensive second pass — some macOS paths reset it on the
        # first run-loop tick.
        AppKit.NSApp.setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyAccessory
        )
        self._request_permissions_upfront()
        self._load_data()
        from ..ai.llm import configure_local_llm, ensure_model_ready

        configure_local_llm(self._config_path)
        ensure_model_ready()
        self._build_status_bar()
        self._build_overlay()
        self._register_hotkey()
        self._spawn_daemon_if_needed()
        self._setup_routine_notifications()
        self._setup_digest_scheduler()

    @objc.python_method
    def _request_permissions_upfront(self) -> None:
        """Trigger Accessibility, Screen Recording and Notification prompts once.

        Guarded by a marker file so returning users aren't re-prompted every
        launch — macOS suppresses already-granted prompts anyway, but the
        marker keeps the first run distinct from later ones for telemetry/
        UI decisions.
        """
        marker = self._data_dir / ".permissions_prompted"
        already = marker.exists()
        try:
            from ..monitor.permissions import request_all_permissions_upfront
            request_all_permissions_upfront()
        except Exception as exc:
            import os
            if os.environ.get("CORENOUS_VERBOSE") == "1":
                print(f"[perms] upfront request failed: {exc}", flush=True)
            return
        if not already:
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("1")
            except Exception:
                pass

    # ── Daemon supervision ────────────────────────────────────────────────────

    @objc.python_method
    def _spawn_daemon_if_needed(self) -> None:
        """Start the background capture daemon if it isn't already running.

        Inside ``Corenous.app`` the daemon shares the bundle binary and is
        launched with the ``--daemon`` argv flag. From the source tree we
        fall back to ``python -m src.monitor.daemon`` via the same helper.
        Either way we double-check the pid file so we never spawn two
        daemons against the same database."""
        import os
        import subprocess

        pid_file = self._data_dir / "daemon.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
            except ValueError:
                pid = None
            if pid is not None:
                try:
                    os.kill(pid, 0)  # signal 0 = liveness probe
                    # Existing daemon is alive — nothing to do.
                    return
                except PermissionError:
                    # Process exists but is owned by a different user; treat as
                    # alive so we don't double-spawn against the same db.
                    return
                except ProcessLookupError:
                    pass  # stale pid; fall through to spawn
            try:
                pid_file.unlink()
            except FileNotFoundError:
                pass

        from ..paths import daemon_spawn_command
        argv = daemon_spawn_command(self._data_dir, self._config_path)
        log_path = self._data_dir / "daemon.log"
        err_path = self._data_dir / "daemon.err"
        try:
            log_f = log_path.open("a")
            err_f = err_path.open("a")
            proc = subprocess.Popen(
                argv,
                cwd=str(self._data_dir),
                stdout=log_f,
                stderr=err_f,
                start_new_session=True,
                close_fds=True,
            )
            log_f.close()
            err_f.close()
            try:
                pid_file.write_text(str(proc.pid))
            except Exception:
                pass
            if os.environ.get("CORENOUS_VERBOSE", "").strip() == "1":
                print(f"[app] spawned daemon pid={proc.pid}", flush=True)
        except Exception as exc:
            if os.environ.get("CORENOUS_VERBOSE", "").strip() == "1":
                print(f"[app] failed to spawn daemon: {exc}", flush=True)

    # ── Routine notifications ─────────────────────────────────────────────────

    @objc.python_method
    def _setup_routine_notifications(self) -> None:
        """Create the RoutineNotificationManager and schedule periodic checks."""
        if self._store is None:
            return
        try:
            from ..routines.notifier import RoutineNotificationManager
            self._routine_manager = RoutineNotificationManager(
                self._store, self._on_routine_execute
            )
        except Exception as exc:
            import os
            if os.environ.get("CORENOUS_VERBOSE") == "1":
                print(f"[routines] notification setup failed: {exc}", flush=True)
            return

        # Check once after a short delay so startup stays snappy, then every
        # 30 minutes so suggestions surface close to their typical time of day.
        AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            45, self, b"_routineTimerFired:", None, False
        )
        self._routine_timer = (
            AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1800, self, b"_routineTimerFired:", None, True
            )
        )

    @objc.typedSelector(b"v@:@")
    def _routineTimerFired_(self, timer):
        if self._routine_manager is not None:
            try:
                self._routine_manager.check_and_notify()
            except Exception:
                pass

    @objc.python_method
    def _on_routine_execute(self, routine) -> None:
        """Called on the main thread when the user clicks Execute in a notification."""
        from ..routines.executor import execute_routine
        ok = execute_routine(routine.action_type, routine.action_data)
        if self._store:
            try:
                if ok:
                    self._store.mark_routine_executed(routine.id)
                else:
                    self._store.mark_routine_dismissed(routine.id)
            except Exception:
                pass
        if self.overlay is not None and hasattr(self.overlay, "_flash_status"):
            try:
                msg = f"Opened {routine.action_data}" if ok else f"Could not open {routine.action_data}"
                self.overlay._flash_status(msg)
            except Exception:
                pass

    # ── Daily digest scheduling ──────────────────────────────────────────────

    @objc.python_method
    def _setup_digest_scheduler(self) -> None:
        """Wire the polled digest scheduler. Generates and notifies once
        per day after the configured local hour."""
        if self._store is None:
            return
        try:
            from ..digest.scheduler import DigestScheduler
            cfg = getattr(self, "_cfg", None) or {}
            digest_cfg = (cfg.get("daily_digest") or {})
            hour = int(digest_cfg.get("delivery_hour", 18))
            self._digest_scheduler = DigestScheduler(
                store=self._store,
                delivery_hour=hour,
                on_delivered=self._on_digest_delivered,
            )
        except Exception as exc:
            import os
            if os.environ.get("CORENOUS_VERBOSE") == "1":
                print(f"[digest] scheduler setup failed: {exc}", flush=True)
            return

        # One probe shortly after launch (catches "app opened after
        # delivery time"), then poll every 5 minutes.
        AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            60, self, b"_digestTimerFired:", None, False
        )
        self._digest_timer = (
            AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                300, self, b"_digestTimerFired:", None, True
            )
        )

    @objc.typedSelector(b"v@:@")
    def _digestTimerFired_(self, timer):
        scheduler = getattr(self, "_digest_scheduler", None)
        if scheduler is None:
            return
        try:
            scheduler.check_and_deliver()
        except Exception:
            pass

    @objc.python_method
    def _on_digest_delivered(self, day_key: str, digest: str) -> None:
        """Worker thread callback. Marshal the notification back to the
        main thread because UNUserNotificationCenter wants AppKit work
        on the main run loop."""
        try:
            from Foundation import NSOperationQueue
            NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda: self._post_digest_notification(day_key, digest)
            )
        except Exception:
            # Last resort: post inline. May not be safe across threads
            # but the alternative is no notification at all.
            try:
                self._post_digest_notification(day_key, digest)
            except Exception:
                pass

    @objc.python_method
    def _post_digest_notification(self, day_key: str, digest: str) -> None:
        """Main thread: schedule a UNUserNotificationCenter alert that
        opens to the overlay when the user taps."""
        try:
            from UserNotifications import (
                UNUserNotificationCenter,
                UNMutableNotificationContent,
                UNNotificationRequest,
                UNTimeIntervalNotificationTrigger,
            )
        except ImportError:
            return
        try:
            content = UNMutableNotificationContent.alloc().init()
            content.setTitle_("Your daily Corenous digest")
            first_line = (digest.splitlines() or [""])[0].strip()
            if len(first_line) > 180:
                first_line = first_line[:177] + "..."
            content.setBody_(first_line or "Tap to view today's recap.")

            trigger = UNTimeIntervalNotificationTrigger.triggerWithTimeInterval_repeats_(
                0.5, False,
            )
            request = UNNotificationRequest.requestWithIdentifier_content_trigger_(
                f"corenous-digest-{day_key}", content, trigger,
            )
            center = UNUserNotificationCenter.currentNotificationCenter()
            if center is not None:
                center.addNotificationRequest_withCompletionHandler_(
                    request, lambda _err: None,
                )
        except Exception as exc:
            import os
            if os.environ.get("CORENOUS_VERBOSE") == "1":
                print(f"[digest] notification post failed: {exc}", flush=True)

    # ── Data layer ────────────────────────────────────────────────────────────

    def _load_data(self):
        import yaml
        from ..memory.store import MemoryStore
        from ..memory.vector_cache import VectorCache
        from ..memory.embedder import Embedder

        try:
            with open(self._config_path) as f:
                cfg = yaml.safe_load(f) or {}
        except FileNotFoundError:
            cfg = {}

        mem_cfg = cfg.get("memory", {})
        db_name  = mem_cfg.get("db_filename",  "memories.db")
        vec_name = mem_cfg.get("vectors_filename", "vectors.npy")

        store = MemoryStore(self._data_dir / db_name)
        # Rebuild FTS index for existing rows (no-op if already populated)
        try:
            store.rebuild_fts()
        except Exception:
            pass

        # Light maintenance on every launch: ANALYZE + FTS optimize keep
        # query latency steady as the corpus grows; VACUUM only fires when
        # the db has accumulated > ~25 MB of free pages (i.e. the user
        # actually deleted things), since VACUUM rewrites the whole file
        # and can take a few seconds on a large db.
        try:
            from sqlite3 import OperationalError
            try:
                fpc_row = store._conn.execute("PRAGMA freelist_count").fetchone()
                psz_row = store._conn.execute("PRAGMA page_size").fetchone()
                free_bytes = int(fpc_row[0]) * int(psz_row[0]) if fpc_row and psz_row else 0
            except OperationalError:
                free_bytes = 0
            if free_bytes > 25 * 1024 * 1024:
                store.compact()
            else:
                store._conn.execute("ANALYZE")
                store._conn.execute(
                    "INSERT INTO memories_fts(memories_fts) VALUES('optimize')"
                )
                store._conn.commit()
        except Exception:
            pass

        cache = VectorCache(self._data_dir / vec_name)
        cache.load_from_store(store.get_all_compressed_vectors())

        self._store    = store
        self._cache    = cache
        self._embedder = Embedder.get()

    # ── Status bar ────────────────────────────────────────────────────────────

    def _build_status_bar(self):
        item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength
        )
        btn = item.button()
        # Use the 22-hole atom mark as a template image so macOS tints it to
        # match the menu-bar theme. Keep the same small footprint we used
        # for the glyph — 18 pt is the standard NSStatusItem height.
        self._mark_image = self._load_status_image()
        if self._mark_image is not None:
            btn.setImage_(self._mark_image)
            btn.setImagePosition_(AppKit.NSImageOnly)
            btn.setTitle_("")
        else:
            btn.setTitle_(self._status_glyph())
        btn.setToolTip_(
            "Corenous AI — click to search · right-click for menu (⌘P pauses capture)"
        )

        target = _MenuTarget.alloc().initWithApp_(self)
        self._menu_target = target   # retain

        # Left-click → toggle overlay directly (no dropdown menu)
        btn.setTarget_(target)
        btn.setAction_(b"handleClick:")
        btn.sendActionOn_(
            AppKit.NSEventMaskLeftMouseUp | AppKit.NSEventMaskRightMouseDown
        )

        # No setMenu_ — that would intercept clicks and show the grey dropdown
        self._status_item = item

    @objc.python_method
    def _status_glyph(self) -> str:
        """Menu-bar glyph fallback when the atom mark image cannot load.

        The live UI uses ``self._mark_image`` plus alpha + tint to express
        live/paused/lite, so this is only ever rendered if the asset is
        missing on disk."""
        if not self._store:
            return "●"
        try:
            paused = self._store.get_config("capture_paused", "0") == "1"
            if paused:
                return "◦"
            lite = self._store.get_config("lite_mode", "0") == "1"
            return "◐" if lite else "●"
        except Exception:
            return "●"

    @objc.python_method
    def _load_status_image(self):
        """Locate the menu-bar template image and configure it as a real
        macOS ``template`` so the system tints it for light/dark menu bars."""
        for parent in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
            cand = parent / "assets" / "corenous-mark-template.png"
            if cand.exists():
                img = AppKit.NSImage.alloc().initWithContentsOfFile_(str(cand))
                if img is None:
                    continue
                img.setSize_(AppKit.NSMakeSize(18, 18))
                img.setTemplate_(True)
                return img
        return None

    @objc.python_method
    def _refresh_status_button_glyph(self) -> None:
        """Reflect capture/lite state on the status item.

        Image-based icon: dim alpha + tint adjustments express the state
        without swapping the artwork, keeping the menu-bar footprint
        identical to the original glyph version. If the image asset is
        missing we degrade gracefully to a text glyph."""
        try:
            btn = self._status_item.button() if self._status_item else None
            if btn is None:
                return
            paused = False
            lite = False
            if self._store is not None:
                try:
                    paused = self._store.get_config("capture_paused", "0") == "1"
                    lite = self._store.get_config("lite_mode", "0") == "1"
                except Exception:
                    paused = lite = False
            if getattr(self, "_mark_image", None) is not None:
                btn.setTitle_("")
                btn.setImage_(self._mark_image)
                btn.setImagePosition_(AppKit.NSImageOnly)
                try:
                    if paused:
                        btn.setAlphaValue_(0.45)
                    elif lite:
                        btn.setAlphaValue_(0.75)
                    else:
                        btn.setAlphaValue_(1.0)
                except Exception:
                    pass
            else:
                btn.setTitle_(self._status_glyph())
        except Exception:
            pass

    # ── Overlay ───────────────────────────────────────────────────────────────

    def _build_overlay(self):
        def search_fn(query: str):
            return combined_search(
                query, self._store, self._cache, self._embedder, top_k=12
            )

        self.overlay = SearchOverlay(
            search_fn,
            self._store,
            data_dir=self._data_dir,
            cache=self._cache,
            config_path=self._config_path,
        )

    # ── Global hotkey ⌥⌘⇧Space ────────────────────────────────────────────────

    def _register_hotkey(self):
        OPT = AppKit.NSEventModifierFlagOption
        CMD = AppKit.NSEventModifierFlagCommand
        SHIFT = AppKit.NSEventModifierFlagShift
        HOTKEY_MODS = OPT | CMD | SHIFT

        def handler(event):
            if event.keyCode() == 49:                    # Space bar
                mods = event.modifierFlags() & AppKit.NSEventModifierFlagDeviceIndependentFlagsMask
                if mods == HOTKEY_MODS:
                    # Must toggle on main thread
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        b"_toggleOverlay", None, False
                    )

        self._hotkey_monitor = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskKeyDown, handler
        )

    @objc.typedSelector(b"v@:@")
    def _toggleOverlay(self, _):
        self.overlay.toggle()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _memory_count_label(self) -> str:
        n = self._store.get_memory_count() if self._store else 0
        v = len(self._store.get_vault_entries()) if self._store else 0
        return f"{n} memories  ·  {v} encrypted"

    def _daemon_label(self) -> str:
        pid_file = self._data_dir / "daemon.pid"
        try:
            import os
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            return "Corenous  Active"
        except Exception:
            return "Corenous  Stopped  —  run corenous-ai start"
