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
    """Show daemon status."""
    app: AppContext = ctx.obj["app"]
    pid_file = app.data_dir / "daemon.pid"
    if _is_running(pid_file):
        click.echo(f"Running  (pid={pid_file.read_text().strip()})")
    else:
        click.echo("Stopped")


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
