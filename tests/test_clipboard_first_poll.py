"""
Tests for ClipboardMonitor first-poll behaviour.

Regression target: the monitor initialised _last_count to None and treated
the very first poll as a change, so whatever sat on the clipboard before
corenous launched (possibly a password) was captured as a fresh memory.
The fix primes the baseline on the first poll and only reports changes from
the next poll onward.
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.monitor.clipboard import ClipboardMonitor


def _fake_appkit(change_count: int, text: str):
    """Build a stand-in AppKit module driven by a settable change count."""
    state = {"count": change_count}

    class _PB:
        def changeCount(self):
            return state["count"]

        def stringForType_(self, _t):
            return text

    class _App:
        def localizedName(self):
            return "TestApp"

    class _WS:
        def frontmostApplication(self):
            return _App()

    mod = types.ModuleType("AppKit")
    mod.NSPasteboard = types.SimpleNamespace(generalPasteboard=lambda: _PB())
    mod.NSStringPboardType = "public.utf8-plain-text"
    mod.NSWorkspace = types.SimpleNamespace(sharedWorkspace=lambda: _WS())
    return mod, state


class TestClipboardFirstPoll(unittest.TestCase):

    def test_first_poll_primes_and_does_not_capture(self):
        appkit, _state = _fake_appkit(change_count=42, text="pre-launch password")
        mon = ClipboardMonitor()
        with mock.patch.dict(sys.modules, {"AppKit": appkit}):
            text, _app, changed = mon._poll()
        self.assertFalse(changed)
        self.assertIsNone(text)

    def test_real_copy_after_first_poll_is_captured(self):
        appkit, state = _fake_appkit(change_count=42, text="old")
        mon = ClipboardMonitor()
        with mock.patch.dict(sys.modules, {"AppKit": appkit}):
            mon._poll()  # prime
            state["count"] = 43  # user copies something new
            text, app, changed = mon._poll()
        self.assertTrue(changed)
        self.assertEqual(text, "old")  # stringForType returns current buffer
        self.assertEqual(app, "TestApp")

    def test_no_change_between_polls_is_not_captured(self):
        appkit, _state = _fake_appkit(change_count=7, text="same")
        mon = ClipboardMonitor()
        with mock.patch.dict(sys.modules, {"AppKit": appkit}):
            mon._poll()      # prime
            _, _, changed = mon._poll()  # count unchanged
        self.assertFalse(changed)


if __name__ == "__main__":
    unittest.main()
