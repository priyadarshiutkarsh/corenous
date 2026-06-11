"""Machine-bound data key for at-rest encryption of local artifacts.

The key is generated once and stored in the user's login Keychain, so files
encrypted with it (the page-content cache today) are unreadable from backups
or copies of the data directory taken off this machine. Tests and headless
environments can inject a key via $CORENOUS_DATA_KEY (64 hex chars) without
touching the Keychain.

This is deliberately separate from the vault: the vault protects flagged
sensitive content behind a passphrase the user must enter; the data key
protects bulk local artifacts transparently, bound to this Mac's login
Keychain rather than to a passphrase.
"""
from __future__ import annotations

import os
import secrets
import subprocess

_SERVICE = "corenous-data-key"
_ACCOUNT = "corenous"
KEY_LEN = 32


def get_data_key() -> bytes | None:
    """Return the 32-byte data key, creating it in the Keychain on first use.

    Returns None when no key is available (locked Keychain, denied access,
    non-macOS). Callers must treat None as "do not write plaintext", never
    as permission to fall back.
    """
    env = os.environ.get("CORENOUS_DATA_KEY", "").strip()
    if env:
        try:
            raw = bytes.fromhex(env)
        except ValueError:
            return None
        return raw if len(raw) == KEY_LEN else None
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-a", _ACCOUNT, "-s", _SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            raw = bytes.fromhex(out.stdout.strip())
            return raw if len(raw) == KEY_LEN else None
        new = secrets.token_bytes(KEY_LEN)
        add = subprocess.run(
            ["security", "add-generic-password",
             "-a", _ACCOUNT, "-s", _SERVICE, "-w", new.hex(), "-U"],
            capture_output=True, text=True, timeout=5,
        )
        if add.returncode == 0:
            return new
    except Exception:
        pass
    return None
