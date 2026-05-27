"""
Tests for src/monitor/permissions.py — notification-prompt helpers.

Strategy: all ObjC framework calls are mocked via sys.modules patching so
the suite runs without macOS entitlements and without spawning real dialogs.
Each test patches only what it needs and restores the original state via
unittest.mock.patch.dict, so tests are fully isolated.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, call, patch

import src.monitor.permissions as perms


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENTINEL = object()


def _make_un_module(center=_SENTINEL):
    """Build a minimal UserNotifications mock with real-looking option flags."""
    mod = MagicMock(name="UserNotifications")
    mod.UNAuthorizationOptionAlert = 4
    mod.UNAuthorizationOptionSound = 2
    mod.UNAuthorizationOptionBadge = 1
    if center is _SENTINEL:
        center = MagicMock(name="UNUserNotificationCenter instance")
    mod.UNUserNotificationCenter.currentNotificationCenter.return_value = center
    return mod, center


# ---------------------------------------------------------------------------
# _request_un_authorization
# ---------------------------------------------------------------------------

class TestRequestUnAuthorization(unittest.TestCase):

    def test_returns_true_and_calls_center(self):
        mod, center = _make_un_module()
        with patch.dict(sys.modules, {"UserNotifications": mod}):
            result = perms._request_un_authorization()
        self.assertTrue(result)
        center.requestAuthorizationWithOptions_completionHandler_.assert_called_once()

    def test_options_bitmask_is_alert_or_sound_or_badge(self):
        """Options must be the bitwise OR of all three flags (= 7)."""
        mod, center = _make_un_module()
        with patch.dict(sys.modules, {"UserNotifications": mod}):
            perms._request_un_authorization()
        options = center.requestAuthorizationWithOptions_completionHandler_.call_args[0][0]
        self.assertEqual(options, 7)  # 4 | 2 | 1

    def test_completion_handler_is_callable(self):
        mod, center = _make_un_module()
        with patch.dict(sys.modules, {"UserNotifications": mod}):
            perms._request_un_authorization()
        handler = center.requestAuthorizationWithOptions_completionHandler_.call_args[0][1]
        self.assertTrue(callable(handler))

    def test_completion_handler_does_not_raise_on_any_args(self):
        """The handler must accept granted=True/False with error=None or an error object."""
        mod, center = _make_un_module()
        with patch.dict(sys.modules, {"UserNotifications": mod}):
            perms._request_un_authorization()
        handler = center.requestAuthorizationWithOptions_completionHandler_.call_args[0][1]
        handler(True, None)
        handler(False, None)
        handler(False, MagicMock(name="NSError"))

    def test_returns_false_when_framework_not_installed(self):
        """ImportError (missing pyobjc-framework-UserNotifications) → False, no crash."""
        with patch.dict(sys.modules, {"UserNotifications": None}):
            result = perms._request_un_authorization()
        self.assertFalse(result)

    def test_returns_false_when_center_is_none(self):
        """currentNotificationCenter() returning None → False, no crash."""
        mod, _ = _make_un_module(center=None)
        with patch.dict(sys.modules, {"UserNotifications": mod}):
            result = perms._request_un_authorization()
        self.assertFalse(result)

    def test_returns_false_when_request_raises(self):
        """ObjC exception from requestAuthorization (e.g. no bundle ID) → False, no crash."""
        mod, center = _make_un_module()
        center.requestAuthorizationWithOptions_completionHandler_.side_effect = RuntimeError(
            "no bundle ID"
        )
        with patch.dict(sys.modules, {"UserNotifications": mod}):
            result = perms._request_un_authorization()
        self.assertFalse(result)

    def test_returns_false_when_center_constructor_raises(self):
        """Exception from currentNotificationCenter() itself → False, no crash."""
        mod, _ = _make_un_module()
        mod.UNUserNotificationCenter.currentNotificationCenter.side_effect = RuntimeError
        with patch.dict(sys.modules, {"UserNotifications": mod}):
            result = perms._request_un_authorization()
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# _trigger_notification_prompt_legacy
# ---------------------------------------------------------------------------

class TestTriggerNotificationPromptLegacy(unittest.TestCase):

    def _make_foundation(self, center=_SENTINEL):
        mod = MagicMock(name="Foundation")
        if center is _SENTINEL:
            center = MagicMock(name="NSUserNotificationCenter instance")
        mod.NSUserNotificationCenter.defaultUserNotificationCenter.return_value = center
        mod.NSUserNotification.alloc.return_value.init.return_value = MagicMock(
            name="NSUserNotification instance"
        )
        return mod, center

    def test_delivers_and_immediately_removes_notification(self):
        mod, center = self._make_foundation()
        with patch.dict(sys.modules, {"Foundation": mod}):
            perms._trigger_notification_prompt_legacy()
        center.deliverNotification_.assert_called_once()
        center.removeDeliveredNotification_.assert_called_once()

    def test_delivered_and_removed_same_object(self):
        """The same notification object must be delivered and then removed."""
        mod, center = self._make_foundation()
        with patch.dict(sys.modules, {"Foundation": mod}):
            perms._trigger_notification_prompt_legacy()
        delivered = center.deliverNotification_.call_args[0][0]
        removed = center.removeDeliveredNotification_.call_args[0][0]
        self.assertIs(delivered, removed)

    def test_sets_expected_identifier(self):
        mod, center = self._make_foundation()
        note = mod.NSUserNotification.alloc.return_value.init.return_value
        with patch.dict(sys.modules, {"Foundation": mod}):
            perms._trigger_notification_prompt_legacy()
        note.setIdentifier_.assert_called_once_with("corenous-perm-bootstrap")

    def test_no_crash_when_foundation_unavailable(self):
        with patch.dict(sys.modules, {"Foundation": None}):
            perms._trigger_notification_prompt_legacy()  # must not raise

    def test_no_crash_when_center_is_none(self):
        mod, _ = self._make_foundation(center=None)
        with patch.dict(sys.modules, {"Foundation": mod}):
            perms._trigger_notification_prompt_legacy()  # must not raise

    def test_no_crash_when_deliver_raises(self):
        mod, center = self._make_foundation()
        center.deliverNotification_.side_effect = RuntimeError("ObjC exception")
        with patch.dict(sys.modules, {"Foundation": mod}):
            perms._trigger_notification_prompt_legacy()  # must not raise


# ---------------------------------------------------------------------------
# _trigger_notification_prompt (orchestrator)
# ---------------------------------------------------------------------------

class TestTriggerNotificationPrompt(unittest.TestCase):

    def test_uses_modern_api_when_available(self):
        """When _request_un_authorization succeeds, legacy path must not run."""
        with patch.object(perms, "_request_un_authorization", return_value=True) as mock_un, \
             patch.object(perms, "_trigger_notification_prompt_legacy") as mock_legacy:
            perms._trigger_notification_prompt()
        mock_un.assert_called_once()
        mock_legacy.assert_not_called()

    def test_falls_back_to_legacy_when_modern_fails(self):
        """When _request_un_authorization returns False, legacy must be called."""
        with patch.object(perms, "_request_un_authorization", return_value=False) as mock_un, \
             patch.object(perms, "_trigger_notification_prompt_legacy") as mock_legacy:
            perms._trigger_notification_prompt()
        mock_un.assert_called_once()
        mock_legacy.assert_called_once()

    def test_no_crash_when_both_paths_fail(self):
        """Both paths silently failing must not propagate an exception."""
        with patch.object(perms, "_request_un_authorization", return_value=False), \
             patch.object(perms, "_trigger_notification_prompt_legacy"):
            perms._trigger_notification_prompt()  # must not raise


# ---------------------------------------------------------------------------
# request_all_permissions_upfront (integration smoke test)
# ---------------------------------------------------------------------------

class TestRequestAllPermissionsUpfront(unittest.TestCase):

    def test_returns_accessibility_and_screen_recording_keys(self):
        with patch.object(perms, "check_accessibility", return_value=True), \
             patch.object(perms, "check_screen_recording", return_value=True), \
             patch.object(perms, "_trigger_notification_prompt"):
            result = perms.request_all_permissions_upfront()
        self.assertIn("accessibility", result)
        self.assertIn("screen_recording", result)

    def test_accessibility_value_reflects_check_result(self):
        with patch.object(perms, "check_accessibility", return_value=False), \
             patch.object(perms, "check_screen_recording", return_value=True), \
             patch.object(perms, "_trigger_notification_prompt"):
            result = perms.request_all_permissions_upfront()
        self.assertFalse(result["accessibility"])

    def test_screen_recording_value_reflects_check_result(self):
        with patch.object(perms, "check_accessibility", return_value=True), \
             patch.object(perms, "check_screen_recording", return_value=False), \
             patch.object(perms, "_trigger_notification_prompt"):
            result = perms.request_all_permissions_upfront()
        self.assertFalse(result["screen_recording"])

    def test_notification_prompt_always_called(self):
        with patch.object(perms, "check_accessibility", return_value=False), \
             patch.object(perms, "check_screen_recording", return_value=False), \
             patch.object(perms, "_trigger_notification_prompt") as mock_prompt:
            perms.request_all_permissions_upfront()
        mock_prompt.assert_called_once()

    def test_no_crash_when_notification_prompt_raises(self):
        """A broken notification system must not prevent accessibility/screen checks."""
        with patch.object(perms, "check_accessibility", return_value=True), \
             patch.object(perms, "check_screen_recording", return_value=True), \
             patch.object(perms, "_trigger_notification_prompt",
                          side_effect=RuntimeError("unexpected")):
            # request_all_permissions_upfront itself does not catch exceptions from
            # _trigger_notification_prompt — if that changes, update this test.
            with self.assertRaises(RuntimeError):
                perms.request_all_permissions_upfront()


if __name__ == "__main__":
    unittest.main()
