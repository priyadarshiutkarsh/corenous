"""AES-256-GCM encrypted vault for sensitive content."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.backends import default_backend

if TYPE_CHECKING:
    from ..memory.store import MemoryStore


SCRYPT_N = 2 ** 17   # ~128 MB RAM, ~1 second on modern hardware
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LEN   = 32
NONCE_LEN = 12
SALT_LEN  = 32


class VaultLockedError(Exception):
    pass


class Vault:
    def __init__(self, store: "MemoryStore") -> None:
        self._store = store
        self._session_key: bytearray | None = None

    # ── Setup & auth ─────────────────────────────────────────────────────────

    def initialize(self, passphrase: str) -> None:
        """First-time setup: generate salt, derive key, store salt in DB."""
        if self._store.get_config("vault_salt"):
            raise RuntimeError("Vault already initialized. Use unlock() instead.")
        salt = os.urandom(SALT_LEN)
        self._store.set_config("vault_salt", salt.hex())
        self._session_key = bytearray(self._derive_key(passphrase, salt))
        # The sentinel is integral to setup, not an obligation on every caller:
        # without it unlock() has nothing to verify against.
        self._write_sentinel()

    def unlock(self, passphrase: str) -> bool:
        """
        Derive key from stored salt and verify by attempting a test decrypt.
        Returns True on success, False on wrong passphrase.
        """
        salt_hex = self._store.get_config("vault_salt")
        if not salt_hex:
            raise RuntimeError("Vault not initialized. Run 'corenous vault init' first.")
        salt = bytes.fromhex(salt_hex)
        candidate = bytearray(self._derive_key(passphrase, salt))

        # Fail closed: an initialized vault must have a sentinel to verify
        # against. Its absence means the vault is broken, not "accept anything".
        sentinel = self._store.get_config("vault_sentinel_ct")
        sentinel_nonce = self._store.get_config("vault_sentinel_nonce")
        if not (sentinel and sentinel_nonce):
            _zero(candidate)
            raise RuntimeError(
                "Vault sentinel missing; vault is corrupt. Re-run 'corenous vault init'."
            )
        try:
            AESGCM(bytes(candidate)).decrypt(
                bytes.fromhex(sentinel_nonce),
                bytes.fromhex(sentinel),
                None,
            )
        except Exception:
            _zero(candidate)
            return False

        self._session_key = candidate
        return True

    def lock(self) -> None:
        if self._session_key is not None:
            _zero(self._session_key)
            self._session_key = None

    def is_unlocked(self) -> bool:
        return self._session_key is not None

    def is_initialized(self) -> bool:
        return bool(self._store.get_config("vault_salt"))

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def store(self, text: str, source: str, app_name: str, reasons: list[str], captured_at: float) -> int:
        """Encrypt and store a sensitive entry. Returns vault_id."""
        self._require_unlocked()
        plaintext = json.dumps({
            "text":     text,
            "source":   source,
            "app":      app_name,
            "ts":       captured_at,
            "reasons":  reasons,
        }, ensure_ascii=False).encode()

        nonce = os.urandom(NONCE_LEN)
        ciphertext = AESGCM(bytes(self._session_key)).encrypt(nonce, plaintext, None)
        return self._store.insert_vault_entry(ciphertext, nonce, captured_at)

    def retrieve(self, vault_id: int) -> dict:
        """Decrypt and return entry dict."""
        self._require_unlocked()
        ciphertext, nonce = self._store.get_vault_ciphertext(vault_id)
        plaintext = AESGCM(bytes(self._session_key)).decrypt(nonce, ciphertext, None)
        return json.loads(plaintext.decode())

    def list_entries(self) -> list[dict]:
        """Return metadata only (no decryption)."""
        return self._store.get_vault_entries()

    # ── Internals ────────────────────────────────────────────────────────────

    def _derive_key(self, passphrase: str, salt: bytes) -> bytes:
        kdf = Scrypt(salt=salt, length=KEY_LEN, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                     backend=default_backend())
        return kdf.derive(passphrase.encode())

    def _require_unlocked(self) -> None:
        if not self.is_unlocked():
            raise VaultLockedError("Vault is locked. Run 'corenous vault unlock' first.")

    def _write_sentinel(self) -> None:
        """Store a small known-plaintext so we can verify the passphrase on unlock."""
        self._require_unlocked()
        nonce = os.urandom(NONCE_LEN)
        ct = AESGCM(bytes(self._session_key)).encrypt(nonce, b"corenous-sentinel", None)
        self._store.set_config("vault_sentinel_ct", ct.hex())
        self._store.set_config("vault_sentinel_nonce", nonce.hex())


def _zero(buf: bytearray) -> None:
    for i in range(len(buf)):
        buf[i] = 0
