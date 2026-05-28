"""
Tests for `corenous-ai daemon start` post spawn verification.

Before this fix, the command sleeps 200 ms after subprocess.Popen and
then unconditionally prints "Corenous started (pid=X)" without checking
whether the spawned process is still alive. A daemon that crashes
during startup (import error, missing config, permission denied) gets
the same success message as a healthy one.

The fix sleeps slightly longer, calls proc.poll(), and surfaces the
daemon.err tail when the process has already exited.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.cli.main import cli


def _invoke_daemon_start(
    *,
    proc_poll_result,
    is_running: bool = False,
    err_tail: str = "",
):
    """Invoke `daemon start` with subprocess.Popen mocked.

    proc_poll_result: what proc.poll() returns. None means still running
    (success path), an integer means exited (failure path).
    """
    proc = MagicMock()
    proc.poll.return_value = proc_poll_result
    proc.pid = 4321
    proc.returncode = proc_poll_result if proc_poll_result is not None else 0

    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        err_path = data_dir / "daemon.err"
        if err_tail:
            err_path.write_text(err_tail)
        # The "already running" path reads the pid file, so create one when
        # we are simulating that state.
        if is_running:
            (data_dir / "daemon.pid").write_text("9999")

        app = MagicMock()
        app.data_dir = data_dir
        app.config_path = data_dir / "settings.yaml"

        runner = CliRunner()
        with patch("src.cli.main.AppContext.load", return_value=app), \
             patch("src.cli.daemon_ctl._is_running", return_value=is_running), \
             patch("src.cli.daemon_ctl.subprocess.Popen", return_value=proc), \
             patch("src.cli.daemon_ctl.subprocess.run"), \
             patch("src.cli.daemon_ctl.time.sleep"):  # don't actually wait
            result = runner.invoke(cli, ["daemon", "start"], catch_exceptions=False)
    return result, proc


class TestDaemonStartVerification(unittest.TestCase):

    def test_prints_pid_when_daemon_stays_alive(self):
        """proc.poll() returning None means the process is still running.
        The CLI should print Corenous started with the pid."""
        result, _ = _invoke_daemon_start(proc_poll_result=None)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Corenous started", result.output)
        self.assertIn("4321", result.output)

    def test_fails_loudly_when_daemon_exits_during_startup(self):
        """proc.poll() returning an integer means the process exited.
        The CLI must surface a failure, not pretend the daemon started."""
        result, _ = _invoke_daemon_start(proc_poll_result=1)
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("failed to start", result.output.lower())
        self.assertIn("exit", result.output.lower())
        self.assertNotIn("Corenous started", result.output)

    def test_failure_message_includes_exit_code(self):
        result, _ = _invoke_daemon_start(proc_poll_result=42)
        self.assertIn("42", result.output)

    def test_failure_message_includes_err_log_tail(self):
        """When daemon.err has content, the last lines must appear in the
        failure message so the user can diagnose the crash without
        hunting through log files."""
        err = (
            "Traceback (most recent call last):\n"
            "  File 'daemon.py', line 17\n"
            "ImportError: No module named 'missing_thing'\n"
        )
        result, _ = _invoke_daemon_start(proc_poll_result=1, err_tail=err)
        self.assertIn("ImportError", result.output)
        self.assertIn("missing_thing", result.output)

    def test_failure_message_without_err_file_still_reports_exit(self):
        """No daemon.err on disk yet (very fast exit). Still must report
        that the daemon failed, just without the tail."""
        result, _ = _invoke_daemon_start(proc_poll_result=2, err_tail="")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("failed to start", result.output.lower())

    def test_does_not_run_when_already_running(self):
        """Pre existing instance — bail before even calling Popen."""
        result, proc = _invoke_daemon_start(
            proc_poll_result=None, is_running=True,
        )
        # Already running path returns without calling Popen, so the proc
        # mock is constructed but never invoked.
        self.assertEqual(result.exit_code, 0)
        self.assertIn("already running", result.output.lower())


if __name__ == "__main__":
    unittest.main()
