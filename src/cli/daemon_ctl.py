"""Start/stop/status control for the background memory daemon."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import click

from .context import AppContext

_PLIST_LABEL = "com.corenous.daemon"
_PLIST_PATH  = Path.home() / "Library" / "LaunchAgents" / f"{_PLIST_LABEL}.plist"
_PID_FILE    = Path("data") / "daemon.pid"


@click.group()
def daemon_group() -> None:
    """Control the background memory capture daemon."""


@daemon_group.command("start")
@click.option("--foreground", is_flag=True, help="Run in foreground (for debugging)")
@click.pass_context
def daemon_start(ctx: click.Context, foreground: bool) -> None:
    """Start the memory capture daemon."""
    app: AppContext = ctx.obj["app"]
    pid_file = app.data_dir / "daemon.pid"

    if _is_running(pid_file):
        click.echo(f"Corenous is already running (pid={pid_file.read_text().strip()}).")
        return

    from ..paths import daemon_spawn_command, IS_BUNDLED
    argv = daemon_spawn_command(app.data_dir, app.config_path)
    cwd = Path.cwd() if not IS_BUNDLED else app.data_dir

    if foreground:
        click.echo("Running in foreground. Press Ctrl+C to stop.")
        os.execvp(argv[0], argv)
        return  # unreachable

    if _PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(_PLIST_PATH)], capture_output=True)

    out = (app.data_dir / "daemon.log").open("a")
    err = (app.data_dir / "daemon.err").open("a")
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdout=out,
        stderr=err,
        start_new_session=True,
        close_fds=True,
    )
    out.close()
    err.close()
    time.sleep(0.2)
    click.echo(f"Corenous started (pid={proc.pid}).")


@daemon_group.command("stop")
@click.pass_context
def daemon_stop(ctx: click.Context) -> None:
    """Stop the memory capture daemon."""
    app: AppContext = ctx.obj["app"]
    pid_file = app.data_dir / "daemon.pid"

    if _PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(_PLIST_PATH)], capture_output=True)
        click.echo("Corenous stopped via launchd.")

    if _is_running(pid_file):
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, signal.SIGTERM)
            click.echo(f"Sent SIGTERM to pid {pid}.")
        except ProcessLookupError:
            click.echo("Process not found; cleaning up pid file.")
        pid_file.unlink(missing_ok=True)
    else:
        click.echo("Corenous is not running.")


@daemon_group.command("status")
@click.pass_context
def daemon_status(ctx: click.Context) -> None:
    """Show daemon status, and warn if the running process is out of sync
    with the source files on disk."""
    app: AppContext = ctx.obj["app"]
    pid_file = app.data_dir / "daemon.pid"
    if not _is_running(pid_file):
        click.echo("Stopped")
        return
    pid = int(pid_file.read_text().strip())
    click.echo(f"Running  (pid={pid})")

    freshness = _check_daemon_freshness(pid)
    if freshness is None:
        return
    if freshness["stale"]:
        started_str = time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(freshness["daemon_start"])
        )
        newest_str = time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(freshness["newest_source_mtime"])
        )
        # Show the source file path relative to the project root so the
        # warning is readable regardless of where the daemon was launched.
        newest_path = freshness["newest_source"]
        rel = str(newest_path)
        if "/src/" in rel:
            rel = "src/" + rel.split("/src/", 1)[1]
        click.echo("")
        click.echo("WARNING: source files on disk are newer than the running daemon.", err=True)
        click.echo(f"  Daemon started  : {started_str}", err=True)
        click.echo(f"  Newest source   : {newest_str}  ({rel})", err=True)
        click.echo("  The daemon is running stale Python bytecode.", err=True)
        click.echo("  Restart it to pick up the changes:", err=True)
        click.echo("    corenous-ai daemon stop && corenous-ai daemon start", err=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _is_running(pid_file: Path) -> bool:
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def _parse_etime(raw: str) -> int | None:
    """Parse the ps ``etime`` format ``[[dd-]hh:]mm:ss`` into total seconds.

    Examples:
        "12:34"          → 754      (12 minutes, 34 seconds)
        "01:23:45"       → 5025     (1 hour, 23 minutes, 45 seconds)
        "02-03:04:05"    → 183845   (2 days, 3 hours, 4 minutes, 5 seconds)

    Returns None when the input does not match the expected shape.
    """
    s = (raw or "").strip()
    if not s:
        return None
    days = 0
    if "-" in s:
        d_str, s = s.split("-", 1)
        try:
            days = int(d_str)
        except ValueError:
            return None
    parts = s.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        hh, (mm, ss) = 0, nums
    elif len(nums) == 3:
        hh, mm, ss = nums
    else:
        return None
    return days * 86400 + hh * 3600 + mm * 60 + ss


def _process_start_time_epoch(pid: int) -> float | None:
    """Return the absolute start time of the given process as a UNIX
    epoch second, or None if the process is not visible or the ps
    invocation fails.

    Uses ``ps -o etime=`` (no trailing ``s``) which prints elapsed time
    in ``[[dd-]hh:]mm:ss`` form. This option is portable across macOS
    and Linux. The Linux-only ``etimes`` keyword would print seconds
    directly but fails on macOS with ``keyword not found``.
    """
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "etime="],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    elapsed = _parse_etime(result.stdout)
    if elapsed is None:
        return None
    return time.time() - elapsed


def _newest_source_file(src_dir: Path) -> tuple[Path, float] | None:
    """Walk ``src_dir`` for ``.py`` files, return (path, mtime) of the
    newest one. Returns None if the directory does not exist or contains
    no Python files."""
    if not src_dir.is_dir():
        return None
    newest_path: Path | None = None
    newest_mtime = 0.0
    for p in src_dir.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > newest_mtime:
            newest_mtime = m
            newest_path = p
    if newest_path is None:
        return None
    return newest_path, newest_mtime


def _check_daemon_freshness(pid: int) -> dict | None:
    """Return a freshness report for the daemon, or None when either the
    process start time or the source tree cannot be resolved.

    Comparing the process start time against the newest ``.py`` mtime in
    the source tree catches the common pitfall where the daemon is a long
    running process and the user has shipped code changes that the
    daemon has not picked up.
    """
    src_dir = Path(__file__).resolve().parent.parent
    start = _process_start_time_epoch(pid)
    if start is None:
        return None
    newest = _newest_source_file(src_dir)
    if newest is None:
        return None
    newest_path, newest_mtime = newest
    return {
        "stale": newest_mtime > start,
        "daemon_start": start,
        "newest_source": newest_path,
        "newest_source_mtime": newest_mtime,
    }


def _venv_python() -> str:
    venv = Path(".venv") / "bin" / "python"
    if venv.exists():
        return str(venv)
    return "python3"


def _write_launchd_plist(python: str, module: str, cwd: Path, app: AppContext) -> None:
    _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>{module}</string>
        <string>--data-dir</string>
        <string>{app.data_dir}</string>
        <string>--config</string>
        <string>{app.config_path}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{cwd}</string>
    <key>RunAtLoad</key>
    <false/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{app.data_dir}/daemon.log</string>
    <key>StandardErrorPath</key>
    <string>{app.data_dir}/daemon.err</string>
</dict>
</plist>
"""
    _PLIST_PATH.write_text(plist)
