"""
Single source of truth for filesystem locations.

The same code runs in three modes:
  1. ``python -m`` from the source tree (``data/`` next to the repo).
  2. The ``corenous-ai`` console script (also from the source tree).
  3. A frozen ``Corenous.app`` bundle built with py2app.

In case 3 we cannot write to the bundle (read-only on Gatekeeper-quarantined
launches) and we cannot rely on ``Path.cwd()`` (Finder launches set cwd to ``/``).
This module figures out the right base directories based on the runtime.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


# ── runtime detection ────────────────────────────────────────────────────────


def _running_from_bundle() -> bool:
    """True iff the current process is the frozen ``Corenous.app`` binary.

    py2app stamps ``sys.frozen`` to ``'macosx_app'`` and lives in a path of
    the form ``Corenous.app/Contents/Resources/__boot__.py``; the
    interpreter is at ``Corenous.app/Contents/MacOS/Python`` (or
    ``Corenous``). Either signal is sufficient."""
    if getattr(sys, "frozen", "") == "macosx_app":
        return True
    try:
        return ".app/Contents/" in str(Path(sys.executable).resolve())
    except Exception:
        return False


IS_BUNDLED: bool = _running_from_bundle()


# ── data directory ──────────────────────────────────────────────────────────


def _bundle_data_dir() -> Path:
    """User-writable data dir for the bundled app.

    ``~/Library/Application Support/Corenous`` is the Apple-blessed location
    for app data that is *not* user documents (no Finder visibility, syncs
    via Time Machine, survives reinstalls)."""
    return Path.home() / "Library" / "Application Support" / "Corenous"


def default_data_dir() -> Path:
    """Where the database, vector cache, and runtime state live."""
    if IS_BUNDLED:
        d = _bundle_data_dir()
    else:
        # Source-tree runtime — preserve historical ``./data`` layout so
        # existing local databases keep working without migration.
        d = Path.cwd() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── config file ─────────────────────────────────────────────────────────────


def _bundle_resources_root() -> Path | None:
    """Best-effort path to ``Corenous.app/Contents/Resources``."""
    if not IS_BUNDLED:
        return None
    # When py2app boots us, ``__file__`` for any in-tree module sits at
    # ``Corenous.app/Contents/Resources/lib/python3.X/...``. Walk up to
    # ``Resources`` regardless of nesting depth.
    here = Path(__file__).resolve()
    for p in here.parents:
        if p.name == "Resources" and p.parent.name == "Contents":
            return p
    return None


def default_config_path() -> Path:
    """Where ``settings.yaml`` lives.

    In a bundle we ship a read-only default at ``Resources/config/settings.yaml``
    AND copy it on first launch to the writable data dir so the user can edit
    it. The data-dir copy wins if it exists."""
    if IS_BUNDLED:
        data_copy = default_data_dir() / "settings.yaml"
        if data_copy.exists():
            return data_copy
        # Copy the bundle default into user data on first launch.
        resources = _bundle_resources_root()
        if resources is not None:
            bundled = resources / "config" / "settings.yaml"
            if bundled.exists():
                try:
                    data_copy.parent.mkdir(parents=True, exist_ok=True)
                    data_copy.write_bytes(bundled.read_bytes())
                    return data_copy
                except Exception:
                    return bundled  # fall back to read-only path
        return data_copy  # may not exist; caller handles missing-config gracefully
    return Path.cwd() / "config" / "settings.yaml"


# ── executable used to respawn the daemon ───────────────────────────────────


def _bundle_executable() -> Path | None:
    """Return ``Corenous.app/Contents/MacOS/<exe>`` when running from a bundle.

    ``sys.executable`` is unreliable inside py2app alias builds (it points
    at the system Python framework), so we ask NSBundle when available
    and otherwise walk up from this file. Returns ``None`` when not in
    a bundle."""
    if not IS_BUNDLED:
        return None
    try:
        from AppKit import NSBundle
        exe = NSBundle.mainBundle().executablePath()
        if exe:
            p = Path(str(exe))
            if p.exists():
                return p
    except Exception:
        pass
    # Fallback: walk from this file up to ``Contents/`` and then into ``MacOS/``.
    here = Path(__file__).resolve()
    for p in here.parents:
        if p.name == "Contents":
            macos = p / "MacOS"
            if macos.is_dir():
                exes = [c for c in macos.iterdir() if c.is_file() and os.access(c, os.X_OK)]
                if exes:
                    return exes[0]
            break
    return None


def daemon_spawn_command(data_dir: Path, config_path: Path) -> list[str]:
    """Argv list that starts the capture daemon as a fresh process.

    In a bundle, the daemon IS the same executable as the menu bar app
    (single binary, dispatched on argv). Out of a bundle, we shell out to
    the venv's Python with ``-m src.monitor.daemon``."""
    if IS_BUNDLED:
        exe = _bundle_executable()
        if exe is not None:
            return [
                str(exe),
                "--daemon",
                "--data-dir", str(data_dir),
                "--config", str(config_path),
            ]
        # Last-ditch fallback — try sys.executable + bundle_entry.
        bundle_entry = Path(__file__).resolve().parent / "bundle_entry.py"
        return [
            sys.executable,
            str(bundle_entry),
            "--daemon",
            "--data-dir", str(data_dir),
            "--config", str(config_path),
        ]
    # Source tree: prefer venv python if we can see one, else fall back to
    # the current interpreter so the spawn always reaches the dependencies.
    venv_py = Path.cwd() / ".venv" / "bin" / "python"
    python = str(venv_py if venv_py.exists() else sys.executable)
    return [
        python,
        "-m", "src.monitor.daemon",
        "--data-dir", str(data_dir),
        "--config", str(config_path),
    ]
