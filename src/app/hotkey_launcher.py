"""Background global hotkey launcher for Corenous.

Runs as a LaunchAgent and listens for ⌥⌘⇧Space. When pressed, it executes
``corenous-ai start`` (via ``python -m src.cli.main start``), so Corenous can
be launched even when the menu-bar app is not already running.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def _launch_corenous(project_root: Path) -> None:
    env = dict(os.environ)
    env.setdefault("CORENOUS_VERBOSE", "0")
    cmd = [sys.executable, "-m", "src.cli.main", "start"]
    try:
        subprocess.Popen(
            cmd,
            cwd=str(project_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception:
        # Silent by design: this helper must never crash the listener loop.
        pass


def run(project_root: Path) -> None:
    import Quartz

    project_root = project_root.resolve()
    last_launch = {"t": 0.0}
    need_flags = (
        Quartz.kCGEventFlagMaskAlternate
        | Quartz.kCGEventFlagMaskCommand
        | Quartz.kCGEventFlagMaskShift
    )

    def callback(proxy, ev_type, event, refcon):  # noqa: ANN001
        # Keep the tap alive if the system disables it temporarily.
        if ev_type in (
            Quartz.kCGEventTapDisabledByTimeout,
            Quartz.kCGEventTapDisabledByUserInput,
        ):
            Quartz.CGEventTapEnable(tap, True)
            return event

        if ev_type != Quartz.kCGEventKeyDown:
            return event

        keycode = Quartz.CGEventGetIntegerValueField(
            event,
            Quartz.kCGKeyboardEventKeycode,
        )
        if int(keycode) != 49:  # Space
            return event

        flags = Quartz.CGEventGetFlags(event)
        if (flags & need_flags) != need_flags:
            return event

        now = time.monotonic()
        if now - float(last_launch["t"]) < 0.9:
            return event
        last_launch["t"] = now
        _launch_corenous(project_root)
        return event

    mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly,
        mask,
        callback,
        None,
    )
    if tap is None:
        _run_appkit_fallback(project_root, last_launch)
        return

    run_loop_source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    loop = Quartz.CFRunLoopGetCurrent()
    Quartz.CFRunLoopAddSource(loop, run_loop_source, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    Quartz.CFRunLoopRun()


def _run_appkit_fallback(project_root: Path, last_launch: dict[str, float]) -> None:
    """Fallback monitor when CGEventTap is unavailable.

    This path keeps the launcher alive; key events still require Accessibility
    permission from macOS, but we avoid crash-looping the LaunchAgent.
    """
    import AppKit
    import Foundation

    opt = AppKit.NSEventModifierFlagOption
    cmd = AppKit.NSEventModifierFlagCommand
    shift = AppKit.NSEventModifierFlagShift
    need = opt | cmd | shift

    def handler(event):  # noqa: ANN001
        try:
            if int(event.keyCode()) != 49:
                return
            mods = (
                event.modifierFlags()
                & AppKit.NSEventModifierFlagDeviceIndependentFlagsMask
            )
            if mods != need:
                return
            now = time.monotonic()
            if now - float(last_launch["t"]) < 0.9:
                return
            last_launch["t"] = now
            _launch_corenous(project_root)
        except Exception:
            return

    AppKit.NSApplication.sharedApplication()
    AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
        AppKit.NSEventMaskKeyDown,
        handler,
    )
    Foundation.NSRunLoop.currentRunLoop().run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Corenous global hotkey launcher")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Project root used as cwd for launching Corenous.",
    )
    args = parser.parse_args()
    run(args.project_root)


if __name__ == "__main__":
    main()
