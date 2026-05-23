"""
Active-window text monitor via macOS Accessibility API (AXUIElement).
Requires Accessibility permission for the running Python binary.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

from .clipboard import CapturedText


# ── Code-aware enrichment ─────────────────────────────────────────────────────
# Matches the most common ways IDEs put a filename in the title bar.
_CODE_APPS = (
    "code", "cursor", "xcode", "pycharm", "webstorm", "intellij idea",
    "rubymine", "phpstorm", "rider", "datagrip", "android studio",
    "sublime text", "atom", "neovide", "zed", "fleet", "windsurf",
)

_FILE_HINT_RE = re.compile(
    r"([\w\-./]+?\.(?:py|js|jsx|ts|tsx|go|rs|java|kt|swift|c|h|hpp|cpp|cs|rb|"
    r"php|m|mm|sh|yml|yaml|json|toml|md|sql|html|css|scss|vue|svelte))",
    re.IGNORECASE,
)

_FN_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][\w]*)\s*\("),       # py
    re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|static\s+|"
               r"async\s+)*function\s+([A-Za-z_$][\w$]*)\s*\("),         # js/ts
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*"
               r"([A-Za-z_$][\w$]*)\s*\("),                              # ts
    re.compile(r"^\s*(?:public|private|internal|protected)?\s*func\s+"
               r"([A-Za-z_][\w]*)\s*\("),                                # swift
    re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_][\w]*)\s*\("),            # rust
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s+)?([A-Za-z_][\w]*)\s*\("),    # go
    re.compile(r"^\s*class\s+([A-Za-z_][\w]*)\s*[:\(\{]?"),              # py/js class
)


def _is_code_editor(app_name: str) -> bool:
    low = (app_name or "").lower()
    return any(k in low for k in _CODE_APPS)


def _extract_filename(window_title: str, app_name: str) -> str:
    """Pull a probable file path/name out of a code editor's title bar.
    Common forms: "settings.py — corenous", "settings.py - corenous - Cursor"."""
    if not window_title:
        return ""
    # Strip trailing app name parts.
    cleaned = window_title
    if app_name:
        for sep in (" — ", " - ", " | ", " · "):
            if cleaned.lower().endswith((sep + app_name).lower()):
                cleaned = cleaned[: -(len(sep) + len(app_name))]
                break
    # Find the first filename-looking segment.
    m = _FILE_HINT_RE.search(cleaned)
    if m:
        return m.group(1)
    # Fallback: take the first " - "-separated segment that looks file-y.
    for part in re.split(r"\s[-—|·]\s", cleaned):
        p = part.strip()
        if p and "." in p and "/" not in p and len(p) <= 64:
            return p
    return ""


def _extract_symbol(text: str) -> str:
    """Walk the captured text and return the first def/class/function-like
    symbol name we can find. Empty string when nothing obvious."""
    if not text:
        return ""
    for raw in text.splitlines()[:60]:
        for pat in _FN_RES:
            m = pat.match(raw)
            if m:
                return m.group(1)
    return ""


def _code_activity(window_title: str, app_name: str, body_text: str) -> str:
    """Build a richer activity label for code editors.

    Returns something like ``"Cursor · settings.py · _build_main"`` when we
    can extract a filename and a symbol, falling back to the parts we have.
    """
    parts: list[str] = []
    if app_name:
        parts.append(app_name)
    fname = _extract_filename(window_title, app_name)
    if fname:
        parts.append(fname)
    sym = _extract_symbol(body_text)
    if sym:
        parts.append(sym)
    if not parts:
        return ""
    return " · ".join(parts)


class WindowMonitor:
    """Watches the focused window. Yields a capture on any of:

    1. Window or app focus changes
    2. The visible text inside the focused window changes
    3. Heartbeat — every ``heartbeat_interval`` seconds, even when nothing
       has changed (so a long reading session still leaves a memory trail).
    """

    def __init__(
        self,
        poll_interval: float = 1.0,
        heartbeat_interval: float = 60.0,
    ) -> None:
        self._poll_interval = poll_interval
        self._heartbeat = max(15.0, float(heartbeat_interval))
        self._last_text: str | None = None
        self._last_app: str = ""
        self._last_yield_at: float = 0.0
        self._ax_available: bool | None = None

    async def stream(self, excluded_apps: list[str] | None = None):
        """Async generator yielding CapturedText when window context changes
        OR every ``heartbeat_interval`` seconds for ambient passive memory."""
        excluded = set(excluded_apps or [])
        loop = asyncio.get_event_loop()
        while True:
            captured = await loop.run_in_executor(None, self._get_focused_text)
            text = captured.text if captured else None
            app_name = captured.app_name if captured else ""
            now = time.time()
            if text and app_name not in excluded:
                changed = (text != self._last_text or app_name != self._last_app)
                heartbeat_due = (now - self._last_yield_at) >= self._heartbeat
                if changed or heartbeat_due:
                    self._last_text = text
                    self._last_app = app_name
                    self._last_yield_at = now
                    yield captured
            await asyncio.sleep(self._poll_interval)

    def _get_focused_text(self) -> CapturedText | None:
        """
        Returns a CapturedText object.
        Tries AXSelectedText first, falls back to AXValue (truncated).
        Returns (None, "") if Accessibility is unavailable or times out.
        """
        try:
            from ApplicationServices import (
                AXUIElementCreateSystemWide,
                AXUIElementCopyAttributeValue,
                kAXErrorSuccess,
            )
            from .app_context import activity_label, get_frontmost_context

            ctx = get_frontmost_context()
            app_name = ctx.app_name
            system_el = AXUIElementCreateSystemWide()

            # Get focused element
            err, focused = AXUIElementCopyAttributeValue(system_el, "AXFocusedUIElement", None)
            if err != kAXErrorSuccess or focused is None:
                return None

            def _activity_for(body: str) -> str:
                if _is_code_editor(app_name):
                    enriched = _code_activity(ctx.window_title, app_name, body)
                    if enriched:
                        return enriched
                return activity_label("window")

            # Try selected text first (least intrusive)
            err, selected = AXUIElementCopyAttributeValue(focused, "AXSelectedText", None)
            if err == kAXErrorSuccess and selected and len(str(selected).strip()) > 5:
                body = str(selected)[:4096]
                return CapturedText(
                    text=body,
                    source="window",
                    app_name=app_name,
                    captured_at=time.time(),
                    window_title=ctx.window_title,
                    bundle_id=ctx.bundle_id,
                    activity=_activity_for(body),
                )

            # Fall back to full field value
            err, value = AXUIElementCopyAttributeValue(focused, "AXValue", None)
            if err == kAXErrorSuccess and value:
                text = str(value)[:4096]
                if len(text.strip()) > 5:
                    return CapturedText(
                        text=text,
                        source="window",
                        app_name=app_name,
                        captured_at=time.time(),
                        window_title=ctx.window_title,
                        bundle_id=ctx.bundle_id,
                        activity=_activity_for(text),
                    )
                return None

            return None

        except ImportError:
            if self._ax_available is None:
                self._ax_available = False
            return None
        except Exception:
            return None
