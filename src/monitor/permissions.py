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
