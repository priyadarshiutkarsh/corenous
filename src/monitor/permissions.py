"""macOS Accessibility and screen-recording permission helpers."""
from __future__ import annotations

import subprocess
import sys


def check_accessibility(prompt: bool = True) -> bool:
    """
    Return True if the current process has Accessibility permission.
    If prompt=True, shows the system dialog asking the user to grant it.
    """
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        from Foundation import NSDictionary
        opts = NSDictionary.dictionaryWithObject_forKey_(prompt, "AXTrustedCheckOptionPrompt")
        return bool(AXIsProcessTrustedWithOptions(opts))
    except ImportError:
        return False


def check_screen_recording(prompt: bool = False) -> bool:
    """Return True if the current process has Screen Recording permission."""
    try:
        import Quartz
        preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        request = getattr(Quartz, "CGRequestScreenCaptureAccess", None)
        if preflight and bool(preflight()):
            return True
        if prompt and request:
            return bool(request())
        return False
    except Exception:
        return False


def open_accessibility_settings() -> None:
    subprocess.run([
        "open",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    ], check=False)


def open_screen_recording_settings() -> None:
    subprocess.run([
        "open",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    ], check=False)


def permission_status(prompt: bool = False) -> dict[str, bool]:
    return {
        "accessibility": check_accessibility(prompt=prompt),
        "screen_recording": check_screen_recording(prompt=prompt),
    }


def all_required_permissions(prompt: bool = False) -> bool:
    status = permission_status(prompt=prompt)
    return status["accessibility"] and status["screen_recording"]


def require_accessibility_or_warn() -> bool:
    """
    Called at daemon startup. Returns True if permission is granted.
    If not, prints guidance and returns False (daemon falls back to clipboard-only).
    """
    granted = check_accessibility(prompt=True)
    if not granted:
        print(
            "[corenous] Accessibility permission not granted.\n"
            "  To enable full window-text capture:\n"
            "  System Settings → Privacy & Security → Accessibility\n"
            "  → add your terminal / Python binary.\n"
            "  Falling back to clipboard-only monitoring.",
            file=sys.stderr,
        )
    return granted


def _request_un_authorization() -> bool:
    """Request notification authorization via UNUserNotificationCenter (macOS 10.14+).

    The authorization request itself shows the system permission dialog — no
    dummy notification delivery required. The completion handler is
    fire-and-forget; we only need the dialog to appear, not the grant result.

    Returns True if the request was dispatched, False if the framework is
    unavailable or the call raises (e.g. running outside a bundle with no
    registered notification ID).
    """
    try:
        from UserNotifications import (
            UNUserNotificationCenter,
            UNAuthorizationOptionAlert,
            UNAuthorizationOptionSound,
            UNAuthorizationOptionBadge,
        )
    except ImportError:
        return False

    try:
        center = UNUserNotificationCenter.currentNotificationCenter()
        if center is None:
            return False

        options = (
            UNAuthorizationOptionAlert
            | UNAuthorizationOptionSound
            | UNAuthorizationOptionBadge
        )

        def _completion(granted, error):
            pass  # fire-and-forget; the system dialog appearing is sufficient

        center.requestAuthorizationWithOptions_completionHandler_(options, _completion)
        return True
    except Exception:
        return False


def _trigger_notification_prompt_legacy() -> None:
    """Deprecated fallback: NSUserNotificationCenter (macOS < 10.14 or no bundle).

    Delivers a dummy NSUserNotification to coax macOS into showing the auth
    dialog, then immediately removes it so the user only sees the prompt.
    NSUserNotificationCenter is deprecated since macOS 11 — this path is only
    reached when _request_un_authorization() fails.
    """
    try:
        from Foundation import NSUserNotification, NSUserNotificationCenter
    except ImportError:
        return
    try:
        center = NSUserNotificationCenter.defaultUserNotificationCenter()
        if center is None:
            return
        note = NSUserNotification.alloc().init()
        note.setIdentifier_("corenous-perm-bootstrap")
        note.setTitle_("Corenous")
        note.setInformativeText_("Setting up notifications…")
        center.deliverNotification_(note)
        center.removeDeliveredNotification_(note)
    except Exception:
        return


def _trigger_notification_prompt() -> None:
    """Request notification authorization, preferring the modern API.

    Tries UNUserNotificationCenter first (macOS 10.14+, non-deprecated).
    Falls back to NSUserNotificationCenter when the framework is unavailable
    or the process has no bundle ID (e.g. running from a terminal).
    """
    if not _request_un_authorization():
        _trigger_notification_prompt_legacy()


def request_all_permissions_upfront() -> dict[str, bool]:
    """Trigger every macOS permission prompt Corenous needs in one pass.

    Called from the menu bar app's applicationDidFinishLaunching so the user
    grants Accessibility, Screen Recording and Notifications in a single
    onboarding moment instead of being interrupted later when each subsystem
    first runs. Returns the resulting status dict so the caller can log it.
    """
    ax = check_accessibility(prompt=True)
    sr = check_screen_recording(prompt=True)
    _trigger_notification_prompt()
    return {"accessibility": ax, "screen_recording": sr}
