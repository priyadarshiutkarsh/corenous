"""Database connection helper with an optional SQLCipher path.

Today the memory database is plain SQLite, protected at rest by FileVault and
0600 permissions (see SECURITY.md). This helper wires the full-database
encryption path so it can become the default in the bundled build, where a
SQLCipher-linked Python can be shipped — without breaking anyone now.

It is OFF by default and opt-in via CORENOUS_ENCRYPT_DB=1, because flipping an
existing plaintext database to SQLCipher is a migration, not a config toggle:
SQLCipher cannot open a plaintext file. When the opt-in is set but the binding
or key is missing, we log and fall back to plain SQLite rather than silently
pretending to encrypt — but never the reverse (we never downgrade an encrypted
file to plaintext).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def _import_sqlcipher():
    for name in ("sqlcipher3", "pysqlcipher3.dbapi2"):
        try:
            mod = __import__(name, fromlist=["connect"])
            return mod
        except Exception:
            continue
    return None


def _data_key_hex() -> str | None:
    try:
        from ..privacy.data_key import get_data_key
        key = get_data_key()
        return key.hex() if key else None
    except Exception:
        return None


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the memory database. Encrypted with SQLCipher when explicitly opted
    in (CORENOUS_ENCRYPT_DB=1) and a binding + Keychain key are available;
    otherwise a plain sqlite3 connection (the current default)."""
    if os.environ.get("CORENOUS_ENCRYPT_DB") == "1":
        binding = _import_sqlcipher()
        key = _data_key_hex()
        if binding is not None and key:
            try:
                conn = binding.connect(str(db_path), check_same_thread=False)
                # Raw-key PRAGMA: no KDF over our already-random 256-bit key.
                conn.execute(f"PRAGMA key = \"x'{key}'\"")
                conn.execute("PRAGMA cipher_version")  # verifies the binding
                return conn
            except Exception as exc:
                print(f"[db] SQLCipher open failed ({exc}); using plain sqlite3",
                      flush=True)
        else:
            missing = "binding" if binding is None else "Keychain key"
            print(f"[db] CORENOUS_ENCRYPT_DB=1 but SQLCipher {missing} "
                  f"unavailable; using plain sqlite3", flush=True)
    return sqlite3.connect(str(db_path), check_same_thread=False)
