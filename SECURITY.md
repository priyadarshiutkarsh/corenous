# Security

Corenous is a local-first memory system: capture, embedding, storage, and
inference all happen on the user's Mac. No telemetry, no cloud sync, no
account. This document describes the at-rest protection model honestly,
including its current limits.

## Data at rest: the layers

| Layer | What it protects | Mechanism |
|-------|------------------|-----------|
| FileVault | Everything, while the machine is powered off or the volume is locked | OS full-volume encryption (on by default on modern Macs) |
| POSIX permissions | The data directory against other local users | `data/` is `0700`, `memories.db` is `0600`, enforced at startup |
| Encrypted vault | Content flagged sensitive (keys, passwords, SSNs, card numbers, user keywords) | AES-256-GCM, key derived from a user passphrase via scrypt (N=2^17); fail-closed unlock |
| Encrypted content cache | Full page, email, and OCR text in `data/content_cache/` | AES-256-GCM envelopes keyed by a random 32-byte key stored in the macOS login Keychain |

The Keychain-bound data key means a copy of the data directory (a backup, a
synced folder, a stolen disk image) is unreadable off this machine for the
content cache, even if FileVault is not in the picture. The key never lives
in the repository, the config, or the data directory.

If no key is available (locked Keychain, denied access), Corenous disables
content-cache writes rather than falling back to plaintext.

## Current limits, stated plainly

- `memories.db` (capture snippets, headings, summaries) is plain SQLite,
  protected by FileVault and POSIX permissions but not application-level
  encryption. Full-database encryption via SQLCipher is on the roadmap; it
  is not shipped today because there are no prebuilt SQLCipher wheels for
  current Python on Apple Silicon, and a source-build requirement would
  hurt more users than it protects.
- `vectors.npy` and the fp16 vectors in the database are not encrypted.
  Embeddings leak coarse semantic information, not raw text.
- Anything running as the same user while the Keychain is unlocked can ask
  for the data key. At-rest encryption protects data copies and other
  accounts, not a compromised live session. No local app can.

## Supply chain

- Top-level dependencies are declared in `pyproject.toml` and
  `requirements.in`; the full tree is pinned with hashes in
  `requirements-lock.txt` (`pip-compile --generate-hashes`).
- `pip-audit` runs in CI on every push and weekly against the lock file.

## The agent boundary

`corenous-ai agent serve` exposes read-only MCP tools. Nothing on that
boundary can mutate or delete memories, error text is sanitized so internal
paths and SQL never reach a client, and vault entries are never served.

## Reporting

Found something? Email upriyadarshi@wisc.edu. Local-first only works if it
is actually private, so reports are taken seriously and credited.
