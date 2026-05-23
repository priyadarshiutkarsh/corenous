"""CLI commands for Corenous global launcher hotkey."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import click

from .context import AppContext


@click.group("hotkey")
def hotkey_group() -> None:
    """Global launcher hotkey (⌥⌘⇧ Space) management on macOS."""


@hotkey_group.command("install")
@click.pass_context
def hotkey_install_cmd(ctx: click.Context) -> None:
    """Install and start the global hotkey launcher (launchd agent)."""
    app_ctx: AppContext = ctx.obj["app"]
    project = Path.cwd().resolve()
    data = app_ctx.data_dir.resolve()
    label = "com.corenous.hotkey"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    python = sys.executable
    launcher = "src.app.hotkey_launcher"
    out_log = data / "hotkey.log"
    err_log = data / "hotkey.err"

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>        <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string><string>{launcher}</string>
        <string>--project-root</string><string>{project}</string>
    </array>
    <key>WorkingDirectory</key>  <string>{project}</string>
    <key>RunAtLoad</key>         <true/>
    <key>KeepAlive</key>         <true/>
    <key>StandardOutPath</key>   <string>{out_log}</string>
    <key>StandardErrorPath</key> <string>{err_log}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')}</string>
    </dict>
</dict>
</plist>"""

    data.mkdir(parents=True, exist_ok=True)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist)

    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    load_cp = subprocess.run(
        ["launchctl", "load", str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if load_cp.returncode != 0:
        raise click.ClickException(
            f"Hotkey launcher install failed: {load_cp.stderr.strip() or load_cp.stdout.strip()}"
        )
    click.echo(f"Installed hotkey launcher → {plist_path}")
    click.echo("Use ⌥⌘⇧ Space to launch Corenous from anywhere.")


@hotkey_group.command("uninstall")
def hotkey_uninstall_cmd() -> None:
    """Stop and remove the global hotkey launcher."""
    label = "com.corenous.hotkey"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if plist_path.exists():
        subprocess.run(
            ["launchctl", "unload", str(plist_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        plist_path.unlink()
        click.echo("Hotkey launcher removed.")
    else:
        click.echo("Hotkey launcher is not installed.")


@hotkey_group.command("status")
def hotkey_status_cmd() -> None:
    """Show whether the global hotkey launcher is active."""
    label = "com.corenous.hotkey"
    cp = subprocess.run(
        ["launchctl", "list", label],
        capture_output=True,
        text=True,
        check=False,
    )
    if cp.returncode == 0:
        out = cp.stdout or ""
        if "\"LastExitStatus\" = 0;" in out:
            click.echo("Hotkey launcher: active (⌥⌘⇧ Space)")
        else:
            click.echo("Hotkey launcher: running but needs permissions")
            click.echo(
                "Allow Accessibility for your Python/Terminal/Cursor process, "
                "then run: corenous-ai hotkey install"
            )
        return
    click.echo("Hotkey launcher: not active")
