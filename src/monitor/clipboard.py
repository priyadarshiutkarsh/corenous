"""
Clipboard monitor using NSPasteboard.
Polls changeCount every poll_interval seconds and yields new text.
No special macOS permission needed for clipboard access.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class CapturedText:
    text: str
    source: str       # 'clipboard' or 'window'
    app_name: str
    captured_at: float
    window_title: str = ""
    bundle_id: str = ""
    activity: str = ""


class ClipboardMonitor:
    def __init__(self, poll_interval: float = 0.5) -> None:
        self._poll_interval = poll_interval
        self._last_count: int | None = None

    async def stream(self, excluded_apps: list[str] | None = None):
        """Async generator yielding CapturedText on each new clipboard write."""
        excluded = set(excluded_apps or [])
        while True:
            text, app_name, changed = self._poll()
            if changed and text and app_name not in excluded:
                from .app_context import activity_label, get_frontmost_context
                ctx = get_frontmost_context()
                yield CapturedText(
                    text=text,
                    source="clipboard",
                    app_name=ctx.app_name or app_name,
                    captured_at=time.time(),
                    window_title=ctx.window_title,
                    bundle_id=ctx.bundle_id,
                    activity=activity_label("clipboard"),
                )
            await asyncio.sleep(self._poll_interval)

    def _poll(self) -> tuple[str | None, str, bool]:
        """Returns (text, app_name, changed)."""
        try:
            from AppKit import NSPasteboard, NSStringPboardType, NSWorkspace
            pb = NSPasteboard.generalPasteboard()
            count = pb.changeCount()
            # First poll only primes the baseline: whatever was on the clipboard
            # before launch (possibly a password) must not be captured as new.
            if self._last_count is None:
                self._last_count = count
                return None, "", False
            changed = count != self._last_count
            self._last_count = count
            if not changed:
                return None, "", False
            text = pb.stringForType_(NSStringPboardType)
            app = NSWorkspace.sharedWorkspace().frontmostApplication().localizedName() or ""
            return (str(text) if text else None), app, True
        except ImportError:
            # Fallback: pyperclip (cross-platform, no app-name info)
            try:
                import pyperclip
                text = pyperclip.paste()
                first = not hasattr(self, "_last_text")
                changed = (not first) and text != self._last_text
                self._last_text = text  # type: ignore[attr-defined]
                return (text or None), "", changed
            except Exception:
                return None, "", False
        except Exception:
            return None, "", False
