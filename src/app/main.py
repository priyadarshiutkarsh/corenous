"""Entry point for the Corenous AI menu bar + overlay app."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import AppKit
from PyObjCTools import AppHelper

from .app_controller import AppController

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[misc, assignment]

APP_INSTANCE_LOCK = "app_instance.lock"

# Keeps the flock(2) lock alive for the process lifetime (released on exit).
_APP_SINGLETON_FP: object | None = None


def app_instance_lock_path(data_dir: Path) -> Path:
    return data_dir / APP_INSTANCE_LOCK


def _cmdline_is_corenous_menu_bar(cmd: str) -> bool:
    """True if argv looks like ``corenous-ai … app`` (menu bar), not ``daemon`` etc."""
    if "corenous-ai app" in cmd:
        return True
    tokens = cmd.split()
    for i, t in enumerate(tokens):
        if t.endswith("corenous-ai") or t == "corenous-ai":
            if i + 1 < len(tokens) and tokens[i + 1] == "app":
                return True
    return False


def _pids_menu_bar_by_argv() -> list[int]:
    """Find menu bar processes by command line (catches stray instances without lock)."""
    me = os.getpid()
    try:
        cp = subprocess.run(
            ["pgrep", "-fl", "corenous-ai"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if cp.returncode != 0 and not (cp.stdout or "").strip():
        return []
    out: list[int] = []
    for line in (cp.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        pid_str, _, cmd = line.partition(" ")
        pid_str = pid_str.strip()
        if not pid_str.isdigit():
            continue
        pid = int(pid_str)
        if pid == me:
            continue
        if _cmdline_is_corenous_menu_bar(cmd.strip()):
            out.append(pid)
    return sorted(set(out))


def _pids_holding_lock_file(lock_path: Path) -> list[int]:
    """Return PIDs with the lock file open (requires ``lsof``, typical on macOS)."""
    if not lock_path.is_file():
        return []
    try:
        cp = subprocess.run(
            ["lsof", "-t", str(lock_path.resolve())],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if cp.returncode != 0 and not (cp.stdout or "").strip():
        return []
    pids: list[int] = []
    for tok in (cp.stdout or "").split():
        if tok.isdigit():
            pids.append(int(tok))
    me = os.getpid()
    return sorted({p for p in pids if p != me})


def stop_existing_app_instances(data_dir: Path) -> list[int]:
    """Stop Corenous menu bar instances for this workspace.

    Targets (1) processes holding ``app_instance.lock`` and (2) any process whose
    argv looks like ``corenous-ai … app``, so duplicate/stray UIs are all cleared.

    Sends SIGTERM, waits, then SIGKILL for survivors still seen via lock or argv scan.
    Returns distinct PIDs we attempted to stop.
    """
    lock_path = app_instance_lock_path(data_dir)
    initial = sorted(
        set(_pids_holding_lock_file(lock_path)) | set(_pids_menu_bar_by_argv())
    )
    if not initial:
        return []

    for pid in initial:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    # Allow AppKit to tear down and release flock.
    time.sleep(0.9)

    stubborn = sorted(
        set(_pids_holding_lock_file(lock_path)) | set(_pids_menu_bar_by_argv())
    )
    for pid in stubborn:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if stubborn:
        time.sleep(0.35)

    return sorted(set(initial) | set(stubborn))


def _find_icon_path() -> Path | None:
    """Locate the Corenous app icon image on disk.

    Order of preference:
      1. Inside a py2app bundle:
         ``Corenous.app/Contents/Resources/Corenous.icns``.
      2. Source tree: ``<repo>/assets/Corenous.icns`` or, as a fallback,
         the raw ``corenous-1024.png`` which NSImage also reads.
    Returns ``None`` if nothing is found — caller falls back to the
    Python rocket icon, which is harmless."""
    here = Path(__file__).resolve()
    # Walk up to find Resources/ (bundle) or repo root (source tree).
    for p in here.parents:
        if p.name == "Resources" and p.parent.name == "Contents":
            cand = p / "Corenous.icns"
            if cand.exists():
                return cand
            break
    for p in here.parents:
        if (p / "assets").is_dir():
            for name in ("Corenous.icns", "corenous-1024.png"):
                cand = p / "assets" / name
                if cand.exists():
                    return cand
            break
    return None


def _set_app_icon(app) -> None:
    """Push our atom logo onto the running ``NSApplication`` instance."""
    icon_path = _find_icon_path()
    if icon_path is None:
        return
    image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(icon_path))
    if image is None:
        return
    app.setApplicationIconImage_(image)


def _acquire_singleton_lock(data_dir: Path) -> bool:
    """Return True if this process should run the UI; False if another instance is active."""
    global _APP_SINGLETON_FP
    if fcntl is None:
        return True
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = app_instance_lock_path(data_dir)
    fp = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fp.close()
        return False
    _APP_SINGLETON_FP = fp
    return True


def launch(data_dir: Path, config_path: Path) -> bool:
    """Start the NSApplication event loop (blocks until quit).

    Returns False if another menu-bar instance is already running (shared ``data_dir``).
    """
    if not _acquire_singleton_lock(data_dir):
        return False

    if os.environ.get("CORENOUS_VERBOSE", "").strip() == "1":
        print(
            "Starting Corenous AI — 🧠 will appear in your menu bar. Press ⌥⌘⇧Space to search.",
            flush=True,
        )

    app = AppKit.NSApplication.sharedApplication()
    # Menu-bar-only app: no Dock entry, no Cmd-Tab presence. Set the
    # activation policy IMMEDIATELY after ``sharedApplication`` so the
    # Python launcher icon never gets a chance to materialize in the
    # Dock during launch.
    try:
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    except Exception:
        # If the activation API is unavailable, fall through; the menu
        # bar app still works, just with a stray dock icon.
        pass

    controller = AppController.alloc().initWithDataDir_configPath_(
        data_dir, config_path
    )
    app.setDelegate_(controller)

    AppHelper.runEventLoop()
    return True
