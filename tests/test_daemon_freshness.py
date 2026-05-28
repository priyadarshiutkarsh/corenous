"""
Tests for the daemon freshness check in src/cli/daemon_ctl.py.

The capture daemon is a long running process. When source code on
disk changes the daemon keeps running stale bytecode until restarted.
`corenous-ai daemon status` now compares the daemon's process start
time against the newest .py file mtime under src/ and warns when the
two have drifted, so the user is not silently testing against the
wrong code path.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.cli.daemon_ctl import (
    _parse_etime,
    _process_start_time_epoch,
    _newest_source_file,
    _check_daemon_freshness,
)
from src.cli.main import cli


# ── _parse_etime ─────────────────────────────────────────────────────────

class TestParseEtime(unittest.TestCase):

    def test_mm_ss_format(self):
        self.assertEqual(_parse_etime("12:34"), 12 * 60 + 34)

    def test_hh_mm_ss_format(self):
        self.assertEqual(_parse_etime("01:23:45"), 3600 + 23 * 60 + 45)

    def test_dd_hh_mm_ss_format(self):
        self.assertEqual(_parse_etime("02-03:04:05"),
                         2 * 86400 + 3 * 3600 + 4 * 60 + 5)

    def test_real_macos_output(self):
        """Real ps output observed on this machine for a 22 hour daemon."""
        self.assertEqual(_parse_etime("22:15:26"),
                         22 * 3600 + 15 * 60 + 26)

    def test_strips_whitespace(self):
        self.assertEqual(_parse_etime("  00:30\n"), 30)

    def test_empty_input_returns_none(self):
        self.assertIsNone(_parse_etime(""))
        self.assertIsNone(_parse_etime("   "))

    def test_malformed_input_returns_none(self):
        self.assertIsNone(_parse_etime("not-a-time"))
        self.assertIsNone(_parse_etime("aa:bb"))
        self.assertIsNone(_parse_etime("1:2:3:4"))


# ── _process_start_time_epoch ────────────────────────────────────────────

class TestProcessStartTime(unittest.TestCase):

    @patch("src.cli.daemon_ctl.time.time", return_value=1_700_000_000.0)
    @patch("src.cli.daemon_ctl.subprocess.run")
    def test_returns_epoch_when_ps_succeeds(self, mock_run, _now):
        """ps prints elapsed time in hh:mm:ss, start_epoch = now minus elapsed."""
        mock_run.return_value = MagicMock(returncode=0, stdout="00:02:00\n")
        result = _process_start_time_epoch(1234)
        self.assertEqual(result, 1_700_000_000.0 - 120)

    @patch("src.cli.daemon_ctl.subprocess.run")
    def test_returns_none_when_ps_returncode_nonzero(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        self.assertIsNone(_process_start_time_epoch(1234))

    @patch("src.cli.daemon_ctl.subprocess.run")
    def test_returns_none_when_ps_output_unparseable(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not a time")
        self.assertIsNone(_process_start_time_epoch(1234))

    @patch("src.cli.daemon_ctl.subprocess.run", side_effect=OSError("no ps"))
    def test_returns_none_when_subprocess_raises(self, _):
        self.assertIsNone(_process_start_time_epoch(1234))


# ── _newest_source_file ──────────────────────────────────────────────────

class TestNewestSourceFile(unittest.TestCase):

    def test_finds_newest_py_file_in_tree(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = root / "old.py"
            new = root / "subdir" / "new.py"
            new.parent.mkdir()
            old.write_text("# old")
            new.write_text("# new")
            os.utime(old, (1000, 1000))
            os.utime(new, (5000, 5000))
            result = _newest_source_file(root)
            self.assertIsNotNone(result)
            path, mtime = result
            self.assertEqual(path, new)
            self.assertAlmostEqual(mtime, 5000, places=0)

    def test_skips_pycache_files(self):
        """Stale .pyc bytecode in __pycache__ must not be considered as
        source change evidence."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "real.py"
            cached = root / "__pycache__" / "stale.cpython-313.pyc"
            cached.parent.mkdir()
            src.write_text("# real")
            cached.write_text("# bytecode")
            os.utime(src, (1000, 1000))
            os.utime(cached, (9999, 9999))
            result = _newest_source_file(root)
            self.assertEqual(result[0], src)
            # Need to skip __pycache__ even if the dir name is a parent,
            # not just the file's parent.
            cached_py = root / "subdir" / "__pycache__" / "x.py"
            cached_py.parent.mkdir(parents=True)
            cached_py.write_text("")
            os.utime(cached_py, (99999, 99999))
            result2 = _newest_source_file(root)
            self.assertEqual(result2[0], src)

    def test_returns_none_when_dir_missing(self):
        self.assertIsNone(_newest_source_file(Path("/no/such/dir")))

    def test_returns_none_when_no_py_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(_newest_source_file(Path(td)))


# ── _check_daemon_freshness ──────────────────────────────────────────────

class TestCheckDaemonFreshness(unittest.TestCase):

    @patch("src.cli.daemon_ctl._newest_source_file")
    @patch("src.cli.daemon_ctl._process_start_time_epoch")
    def test_stale_when_source_newer_than_daemon(self, mock_start, mock_newest):
        mock_start.return_value = 1000.0
        mock_newest.return_value = (Path("/src/foo.py"), 2000.0)
        result = _check_daemon_freshness(1234)
        self.assertIsNotNone(result)
        self.assertTrue(result["stale"])

    @patch("src.cli.daemon_ctl._newest_source_file")
    @patch("src.cli.daemon_ctl._process_start_time_epoch")
    def test_fresh_when_daemon_started_after_source(self, mock_start, mock_newest):
        mock_start.return_value = 2000.0
        mock_newest.return_value = (Path("/src/foo.py"), 1000.0)
        result = _check_daemon_freshness(1234)
        self.assertFalse(result["stale"])

    @patch("src.cli.daemon_ctl._process_start_time_epoch", return_value=None)
    def test_returns_none_when_process_unknown(self, _):
        """If ps fails, the check returns None rather than guessing."""
        self.assertIsNone(_check_daemon_freshness(1234))

    @patch("src.cli.daemon_ctl._newest_source_file", return_value=None)
    @patch("src.cli.daemon_ctl._process_start_time_epoch", return_value=1000.0)
    def test_returns_none_when_source_dir_missing(self, _start, _src):
        self.assertIsNone(_check_daemon_freshness(1234))


# ── daemon status CLI integration ────────────────────────────────────────

class TestDaemonStatusCommand(unittest.TestCase):

    def _invoke(self, args: list[str], *, running: bool, freshness: dict | None):
        app = MagicMock()
        app.data_dir = Path("/tmp/test-corenous")
        runner = CliRunner()
        with patch("src.cli.main.AppContext.load", return_value=app), \
             patch("src.cli.daemon_ctl._is_running", return_value=running), \
             patch("src.cli.daemon_ctl.Path.read_text", return_value="1234"), \
             patch("src.cli.daemon_ctl._check_daemon_freshness", return_value=freshness):
            return runner.invoke(cli, args, catch_exceptions=False)

    def test_prints_stopped_when_not_running(self):
        result = self._invoke(["daemon", "status"], running=False, freshness=None)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Stopped", result.output)
        # No stale-warning noise when daemon isn't running.
        self.assertNotIn("WARNING", result.output)

    def test_prints_running_pid_when_fresh(self):
        fresh = {"stale": False, "daemon_start": 0, "newest_source": Path("x.py"),
                 "newest_source_mtime": 0}
        result = self._invoke(["daemon", "status"], running=True, freshness=fresh)
        self.assertIn("Running", result.output)
        self.assertIn("1234", result.output)
        self.assertNotIn("WARNING", result.output)

    def test_warns_when_stale(self):
        """Source mtime newer than daemon start time → user sees a clear
        warning telling them to restart, plus the offending source path."""
        stale = {
            "stale": True,
            "daemon_start": 1_700_000_000.0,
            "newest_source": Path("/Users/me/corenous/src/ai/summarizer.py"),
            "newest_source_mtime": 1_700_000_500.0,
        }
        result = self._invoke(["daemon", "status"], running=True, freshness=stale)
        self.assertIn("Running", result.output)
        self.assertIn("WARNING", result.output)
        self.assertIn("src/ai/summarizer.py", result.output)
        self.assertIn("daemon stop", result.output)

    def test_silent_when_freshness_check_fails(self):
        """If we can't resolve ps or source dir, do NOT print a misleading
        warning. Just show the basic running line."""
        result = self._invoke(["daemon", "status"], running=True, freshness=None)
        self.assertIn("Running", result.output)
        self.assertNotIn("WARNING", result.output)


if __name__ == "__main__":
    unittest.main()
