"""
Tests for the optional-SQLCipher connection helper.

The contract that matters today: the default path is plain sqlite3 (unchanged
behavior), and opting in without the binding falls back to plain sqlite3 with a
warning rather than failing or silently claiming encryption.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory import db as dbmod
from src.memory.store import MemoryStore


class TestConnect(unittest.TestCase):

    def test_default_is_plain_sqlite_and_usable(self):
        with mock.patch.dict(os.environ, {"CORENOUS_ENCRYPT_DB": ""}):
            with tempfile.TemporaryDirectory() as tmp:
                conn = dbmod.connect(Path(tmp) / "m.db")
                conn.execute("CREATE TABLE t (x INTEGER)")
                conn.execute("INSERT INTO t VALUES (1)")
                self.assertEqual(conn.execute("SELECT x FROM t").fetchone()[0], 1)

    def test_optin_without_binding_falls_back(self):
        with mock.patch.dict(os.environ, {"CORENOUS_ENCRYPT_DB": "1"}):
            with mock.patch("src.memory.db._import_sqlcipher", return_value=None):
                with tempfile.TemporaryDirectory() as tmp:
                    conn = dbmod.connect(Path(tmp) / "m.db")
                    # Falls back to a working plain connection, not an exception.
                    conn.execute("CREATE TABLE t (x INTEGER)")
                    self.assertEqual(
                        conn.execute("SELECT count(*) FROM t").fetchone()[0], 0)

    def test_store_still_works_through_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "m.db")
            self.assertEqual(store.get_memory_count(), 0)


if __name__ == "__main__":
    unittest.main()
