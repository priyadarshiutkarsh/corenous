# Changelog

All notable changes to Corenous AI are documented here. This project follows
[Keep a Changelog](https://keepachangelog.com/) and
[Semantic Versioning](https://semver.org/).

## [Unreleased]

A security and retrieval-quality release: the vault now actually protects
flagged captures, bulk local artifacts are encrypted at rest, and search got
measurably better on the queries people really type.

### Security

- **Write-only sealed vault.** Flagged captures are now encrypted to the
  vault's X25519 public key (ECIES: X25519 + HKDF + AES-256-GCM), so the
  capture daemon protects them the moment they happen without ever holding the
  passphrase. This fixes a cross-process hole where `vault unlock` only
  unlocked the CLI's own process and every flagged capture was silently
  dropped. Legacy vaults upgrade automatically on their next unlock; legacy
  entries stay readable.
- **Encrypted page-content cache.** Full page, email, and OCR text in
  `data/content_cache/` is now stored as AES-256-GCM envelopes keyed by a
  random key in the macOS login Keychain. No key means no write — never a
  plaintext fallback. Copies of the data directory are unreadable off-machine.
- **Screenshot lifecycle.** Captured screenshots are deleted as soon as the
  vision pass consumes them instead of lingering in the cache, and the cache
  directory is owner-only.
- **Permission hardening.** `data/` is `0700` and `memories.db` is `0600`,
  enforced at startup. New `SECURITY.md` documents the full at-rest model,
  including its limits.
- **Sensitivity detection fixes.** Keyword matching is word-bounded ("ein" no
  longer matches inside "being", "hiv" inside "archive"), and an AI check
  skipped because the model was busy is re-run by a background loop instead of
  silently passing — flagged results are sealed into the vault. Missed and
  re-vaulted counts surface in `corenous-ai daemon status`.
- **Supply chain.** `pyproject.toml` and `requirements.txt` now declare the
  real dependency set (the Vision/Quartz/UserNotifications frameworks and MLX
  were missing; the removed llama-cpp-python lingered). The full tree is
  hash-pinned in `requirements-lock.txt` and `pip-audit` runs in CI on every
  push and weekly. Local audit went from 10 known vulnerabilities to 1
  (torch, no upstream fix yet).

### Added

- **First-run vault setup** — the app asks for a vault passphrase once at
  launch, and the onboarding tour gained a "try it right now" step that copies
  a test sentence so the first search has something to find.
- **Trust controls in the menu bar**: per-app capture exclusion (live, no
  restart), Erase All Memories with a destructive-action confirm, and a
  "capture paused" indicator in the menu header.
- **Context envelope at capture time** — embeddings are wrapped with window,
  app, date, and previous-heading context; `corenous-ai reindex` applies the
  same envelope to existing memories.
- **Evaluation harnesses**: LoCoMo answer-accuracy (J score) with independent
  generator/judge selection, and checkpoint/resume for the LongMemEval run.

### Changed

- **Cross-encoder reranking on the default search path** — previously only
  deep search and the MCP server used it; measured to roughly double MRR.
- **overlay.py split into three layers** (theme, widgets, controller) with no
  behavior change, verified by the full test suite.
- **Overlay polish**: quieter quote, a Search empty state that teaches example
  and temporal queries, honest digest placeholder, unambiguous provider
  toggle, and timeline rows that show the model's kicker line.

### Fixed

- **Hybrid search lexical recall**: FTS5 terms were implicitly ANDed, so a
  natural-language question matched only memories containing every word —
  usually none. Terms are now ORed under BM25 ranking (measured +0.8
  recall@10, +1.2 MRR points on LoCoMo, on top of fixing zero-result
  questions).
- README claims corrected (embedding model name; the 58-byte figure is the
  in-RAM index entry, with fp16 vectors on disk for re-ranking).
- Package version aligned (pyproject said 0.0.1 while the README shipped
  0.1.0).

## [0.1.0] — 2026-06-01

The first feature release since the initial cut. Corenous now runs on a single
local vision-language model, ships a redesigned memory experience, and exposes a
proper read-only MCP server for AI agents.

### Added

- **Read-only MCP server**, rebuilt on the official MCP Python SDK (FastMCP) and
  served over stdio via `corenous-ai agent serve`. Agents can `search_memories`,
  `list_recent_memories`, `get_memory`, and `find_related_memories`, plus read a
  `corenous://stats` resource. Every tool is read-only, sanitises its errors so
  internal paths and SQL never reach the client, and skips memories routed to the
  encrypted vault.
- **Related memories** in the memory detail view — semantic neighbours surfaced
  from the compressed vector cache so you can follow a thread.
- **Daily digest** — a per-day session summary with key moments, cached for
  instant reads, delivered through a macOS notification scheduler. New CLI:
  `memories digest`, `memories sessions`, and `memories changed-today`.
- **Fast scripting commands** for the terminal: `search`, `recent`, and `tail`.

### Changed

- **One local model for everything.** The separate Llama 3.2 3B GGUF text model
  is gone; all inference — capture summaries, digests, sensitivity checks, and
  chat — now routes through a single 4-bit **Qwen2.5-VL 3B** running on Apple MLX
  (Metal). On an 8 GB Mac there is no longer a second set of weights competing for
  the GPU. Screenshots are summarised directly by the vision model.
- **Redesigned memory detail page.** The view leads with the thought rather than
  metadata, groups content into real sections, hides the raw capture behind a
  toggle, and pins a centered source · date · time byline to the foot of the panel.
- **Refreshed type system and overlay polish** — a tighter Futura/Avenir scale,
  tabular figures for counts and timestamps, VoiceOver labels on custom controls,
  hover tooltips that reveal truncated text, and reduced-motion support.
- **Sharper capture quality.** UI chrome is stripped before summarisation, OCR
  uses Vision bounding boxes to drop top and bottom window chrome, duplicate page
  captures are merged across sensors, and low-signal pages skip AI refinement to
  avoid echoing the screen back at you.
- **Smoother background AI.** Refinement is paced so the GPU stays responsive while
  a capture backlog drains, and the vision model's input resolution is capped to
  roughly halve summary latency.

### Fixed

- Vault unlock no longer accepts any passphrase when the sentinel is missing.
- The MCP server no longer leaks internal exception text to clients.
- FTS5 double-delete corruption in `delete_memory`.
- Clipboard monitor no longer captures pre-launch clipboard content on first poll.

[0.1.0]: https://github.com/priyadarshiutkarsh/corenous/releases/tag/v0.1.0
[0.0.1]: https://github.com/priyadarshiutkarsh/corenous/releases/tag/v0.0.1
