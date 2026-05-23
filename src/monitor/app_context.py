"""Frontmost macOS app/window context helpers."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


@dataclass
class FrontmostAppContext:
    app_name: str = ""
    bundle_id: str = ""
    pid: int = 0
    window_title: str = ""


@dataclass
class BrowserTabContext:
    title: str = ""
    url: str = ""
    domain: str = ""
    query: str = ""

    @property
    def is_new_tab(self) -> bool:
        low_title = self.title.lower().strip()
        low_url = self.url.lower().strip()
        return (
            low_title in {"new tab", "start page", "untitled"}
            or low_url == "about:blank"
            or low_url.startswith(("chrome://newtab", "edge://newtab", "brave://newtab", "arc://newtab"))
        )


_APP_CATEGORIES: dict[str, str] = {
    "slack": "chat", "discord": "chat", "telegram": "chat",
    "messages": "chat", "whatsapp": "chat", "signal": "chat",
    "chrome": "browser", "firefox": "browser", "safari": "browser",
    "brave browser": "browser", "arc": "browser", "edge": "browser",
    "visual studio code": "code editor", "code": "code editor",
    "cursor": "code editor", "xcode": "code editor",
    "intellij idea": "code editor", "pycharm": "code editor",
    "webstorm": "code editor", "android studio": "code editor",
    "notion": "notes", "obsidian": "notes", "notes": "notes",
    "word": "document", "excel": "spreadsheet", "pages": "document",
    "numbers": "spreadsheet", "keynote": "presentation",
    "mail": "email", "outlook": "email", "superhuman": "email",
    "terminal": "terminal", "iterm2": "terminal", "warp": "terminal",
    "hyper": "terminal", "kitty": "terminal",
    "figma": "design", "sketch": "design",
    "zoom": "meeting", "teams": "meeting", "meet": "meeting",
}


def app_slug(app_name: str, bundle_id: str = "") -> str:
    seed = app_name or bundle_id or "unknown-app"
    slug = re.sub(r"[^a-z0-9]+", " ", seed.lower()).strip()
    return slug or "unknown app"


def app_category(app_name: str) -> str:
    key = (app_name or "").lower().strip()
    for name, category in _APP_CATEGORIES.items():
        if name in key:
            return category
    return "mac app"


def app_tags(app_name: str, bundle_id: str = "") -> str:
    slug = app_slug(app_name, bundle_id)
    category = app_category(app_name)
    return slug if slug == category else f"{slug} {category}"


def is_browser_app(app_name: str, bundle_id: str = "") -> bool:
    seed = f"{app_name} {bundle_id}".lower()
    return any(name in seed for name in (
        "chrome", "chromium", "safari", "firefox", "brave", "arc", "edge",
    ))


def canonical_browser_name(app_name: str, bundle_id: str = "") -> str | None:
    """Return the AppleScript application name when *app_name* is a browser.

    Returns ``None`` when the app is not a supported browser. Used to gate
    browser tab scraping so we only read tabs from the macOS frontmost app.
    """
    if not is_browser_app(app_name, bundle_id):
        return None
    low = (app_name or "").lower()
    if "safari" in low:
        return "Safari"
    for candidate in ("Google Chrome", "Brave Browser", "Microsoft Edge", "Arc"):
        if candidate.lower() in low or low in candidate.lower():
            return candidate
    return app_name.strip() or None


def _browser_script(app_name: str) -> str | None:
    low = app_name.lower()
    if "safari" in low:
        return (
            'tell application "Safari"\n'
            'if not (exists front window) then return ""\n'
            'set t to name of current tab of front window\n'
            'set u to URL of current tab of front window\n'
            'return t & "\\n" & u\n'
            'end tell'
        )
    target = None
    for candidate in ("Google Chrome", "Brave Browser", "Microsoft Edge", "Arc"):
        if candidate.lower() in low or low in candidate.lower():
            target = candidate
            break
    if target is None:
        return None
    return (
        f'tell application "{target}"\n'
        'if not (exists front window) then return ""\n'
        'set t to title of active tab of front window\n'
        'set u to URL of active tab of front window\n'
        'return t & "\\n" & u\n'
        'end tell'
    )


def _parse_browser_url(title: str, url: str) -> BrowserTabContext:
    parsed = urlparse(url)
    domain = (parsed.netloc or parsed.path).replace("www.", "").lower()
    query = ""
    params = parse_qs(parsed.query)
    for key in ("q", "query", "search", "p"):
        if params.get(key):
            query = params[key][0]
            break
    return BrowserTabContext(title=title.strip(), url=url.strip(), domain=domain, query=query.strip())


def get_browser_tab_context(ctx: FrontmostAppContext) -> BrowserTabContext:
    """Best-effort active browser tab title/URL, kept fast and optional."""
    if not is_browser_app(ctx.app_name, ctx.bundle_id):
        return BrowserTabContext()
    script = _browser_script(ctx.app_name)
    if not script:
        return BrowserTabContext(title=ctx.window_title)
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=0.55,
            check=False,
        )
    except Exception:
        return BrowserTabContext(title=ctx.window_title)
    if result.returncode != 0:
        return BrowserTabContext(title=ctx.window_title)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return BrowserTabContext(title=ctx.window_title)
    title = lines[0]
    url = lines[1] if len(lines) > 1 else ""
    return _parse_browser_url(title, url)


def browser_activity(tab: BrowserTabContext) -> str:
    if tab.query:
        return "Searched Web"
    if tab.domain:
        # Strip library proxy suffixes (ezproxy, etc.)
        domain = re.sub(r"\.(ezproxy|proxy|remotexs)\..+$", "", tab.domain, flags=re.IGNORECASE)
        parts = domain.split(".")
        if len(parts) >= 2:
            name = parts[-2].title()
            tld  = parts[-1].lower()
            display = f"{name}.{tld}"
        elif "-" in domain:
            # Handle hyphenated proxy domains: www-jstor-org → Jstor.org
            hp = domain.lower().replace("www-", "").split("-")
            if len(hp) >= 2:
                display = f"{hp[-2].title()}.{hp[-1]}"
            else:
                display = hp[0].title()
        else:
            display = domain.title()
        return f"Browsed {display}" if display else "Browsed Website"
    return "Browsed Website"


def activity_label(source: str) -> str:
    return {
        "clipboard": "Copied text",
        "window": "Focused text",
        "screen": "Read screen",
        "browser": "Browsed page",
        "manual": "Manual memory",
    }.get((source or "").lower(), "Captured text")


def _frontmost_pid_via_applescript() -> tuple[str, int]:
    """Use osascript to get the truly frontmost app name and PID. Works from any process."""
    try:
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events"\n'
             'set fp to first application process whose frontmost is true\n'
             'return (name of fp) & "\\n" & (unix id of fp)\n'
             'end tell'],
            capture_output=True, text=True, timeout=1.0, check=False,
        )
        if result.returncode == 0:
            lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
            if len(lines) >= 2:
                return lines[0], int(lines[1])
            if lines:
                return lines[0], 0
    except Exception:
        pass
    return "", 0


def get_frontmost_context() -> FrontmostAppContext:
    """Return app name, bundle id, pid, and best-effort front window title."""
    ctx = FrontmostAppContext()

    # Step 1: get frontmost app via AppleScript (reliable from background processes)
    app_name, pid = _frontmost_pid_via_applescript()
    if not app_name:
        return ctx
    ctx.app_name = app_name
    ctx.pid = pid

    # Step 2: bundle ID from NSWorkspace running apps
    try:
        import AppKit
        for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
            if (pid and int(app.processIdentifier()) == pid) or \
               str(app.localizedName() or "").lower() == app_name.lower():
                ctx.bundle_id = str(app.bundleIdentifier() or "")
                if not ctx.pid:
                    ctx.pid = int(app.processIdentifier())
                break
    except Exception:
        pass

    if not ctx.pid:
        return ctx

    # Step 3: window title from Quartz (largest layer-0 window for that PID)
    try:
        import Quartz
        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        ) or []
        best_title = ""
        best_area = 0
        for info in windows:
            if info.get("kCGWindowOwnerPID") != ctx.pid:
                continue
            if info.get("kCGWindowLayer", 99) != 0:
                continue
            bounds = info.get("kCGWindowBounds", {})
            area = bounds.get("Width", 0) * bounds.get("Height", 0)
            title = str(info.get("kCGWindowName") or "")
            if area > best_area:
                best_area = area
                best_title = title
        ctx.window_title = best_title
    except Exception:
        pass

    return ctx
