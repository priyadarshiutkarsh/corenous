"""AES-256-GCM encrypted vault for sensitive content.

Two write paths:

- ``store()``  — legacy symmetric write; requires an unlocked session.
- ``seal()``   — write-only ECIES sealed box (X25519 + HKDF + AES-256-GCM)
  to the vault's public key. No passphrase, no unlock, no secret in the
  calling process. This is what the capture daemon uses: it can PROTECT a
  flagged capture at the moment it happens, but can never read it back.
  Reads always require the passphrase, which unwraps the private key.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
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

_SEAL_MAGIC = b"CV2"          # sealed-entry blob prefix
_SEAL_HKDF_INFO = b"corenous-vault-seal-v2"


class VaultLockedError(Exception):
    pass


class Vault:
    def __init__(self, store: "MemoryStore") -> None:
        self._store = store
        self._session_key: bytearray | None = None
        self._seal_priv: X25519PrivateKey | None = None

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
        self._ensure_seal_keypair()

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
        # Vaults initialized before sealing existed get their keypair on the
        # first successful unlock (the only moment we hold the session key).
        try:
            self._ensure_seal_keypair()
        except Exception:
            pass
        return True

    def lock(self) -> None:
        if self._session_key is not None:
            _zero(self._session_key)
            self._session_key = None
        self._seal_priv = None

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

    def can_seal(self) -> bool:
        """True when write-only sealed storage is available (no unlock needed).
        Requires a vault initialized with, or upgraded to, the sealing keypair."""
        return bool(self._store.get_config("vault_seal_pub"))

    def seal(self, text: str, source: str, app_name: str, reasons: list[str],
             captured_at: float) -> int:
        """Encrypt and store a sensitive entry WITHOUT the session key.

        ECIES sealed box: an ephemeral X25519 key agrees with the vault's
        public key, HKDF derives an AES-256-GCM key, and the ephemeral public
        key rides in the blob. The sealing process never holds anything that
        can decrypt — only unlock() + retrieve() can read this back."""
        pub_hex = self._store.get_config("vault_seal_pub")
        if not pub_hex:
            raise VaultLockedError(
                "Vault has no sealing key. Run 'corenous vault init', or unlock "
                "once to upgrade an older vault."
            )
        plaintext = json.dumps({
            "text":     text,
            "source":   source,
            "app":      app_name,
            "ts":       captured_at,
            "reasons":  reasons,
        }, ensure_ascii=False).encode()
        pub = X25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        eph = X25519PrivateKey.generate()
        key = HKDF(
            algorithm=hashes.SHA256(), length=KEY_LEN, salt=None,
            info=_SEAL_HKDF_INFO,
        ).derive(eph.exchange(pub))
        nonce = os.urandom(NONCE_LEN)
        ct = AESGCM(key).encrypt(nonce, plaintext, None)
        eph_pub = eph.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        return self._store.insert_vault_entry(
            _SEAL_MAGIC + eph_pub + ct, nonce, captured_at,
        )

    def retrieve(self, vault_id: int) -> dict:
        """Decrypt and return entry dict (sealed v2 or legacy symmetric)."""
        self._require_unlocked()
        ciphertext, nonce = self._store.get_vault_ciphertext(vault_id)
        ciphertext = bytes(ciphertext)
        if ciphertext.startswith(_SEAL_MAGIC):
            priv = self._unwrap_seal_priv()
            eph_pub = X25519PublicKey.from_public_bytes(
                ciphertext[len(_SEAL_MAGIC):len(_SEAL_MAGIC) + 32],
            )
            key = HKDF(
                algorithm=hashes.SHA256(), length=KEY_LEN, salt=None,
                info=_SEAL_HKDF_INFO,
            ).derive(priv.exchange(eph_pub))
            plaintext = AESGCM(key).decrypt(
                bytes(nonce), ciphertext[len(_SEAL_MAGIC) + 32:], None,
            )
        else:
            plaintext = AESGCM(bytes(self._session_key)).decrypt(
                bytes(nonce), ciphertext, None,
            )
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

    def _ensure_seal_keypair(self) -> None:
        """Create the sealing keypair if missing. Requires an unlocked session:
        the public key is stored plain (it can only ENCRYPT), the private key
        is wrapped with the session key so reads keep requiring the passphrase."""
        self._require_unlocked()
        if self._store.get_config("vault_seal_pub"):
            return
        priv = X25519PrivateKey.generate()
        priv_raw = priv.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        pub_raw = priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        nonce = os.urandom(NONCE_LEN)
        wrapped = AESGCM(bytes(self._session_key)).encrypt(nonce, priv_raw, None)
        self._store.set_config("vault_seal_pub", pub_raw.hex())
        self._store.set_config("vault_seal_priv_wrapped", wrapped.hex())
        self._store.set_config("vault_seal_priv_nonce", nonce.hex())

    def _unwrap_seal_priv(self) -> X25519PrivateKey:
        """Unwrap the sealing private key with the session key (cached until lock)."""
        self._require_unlocked()
        if self._seal_priv is not None:
            return self._seal_priv
        wrapped = self._store.get_config("vault_seal_priv_wrapped")
        nonce = self._store.get_config("vault_seal_priv_nonce")
        if not (wrapped and nonce):
            raise VaultLockedError(
                "Vault has no sealing keypair; unlock once to upgrade it."
            )
        raw = AESGCM(bytes(self._session_key)).decrypt(
            bytes.fromhex(nonce), bytes.fromhex(wrapped), None,
        )
        self._seal_priv = X25519PrivateKey.from_private_bytes(raw)
        return self._seal_priv


def _zero(buf: bytearray) -> None:
    for i in range(len(buf)):
        buf[i] = 0
