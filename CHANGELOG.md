# Changelog

All notable changes to Corenous AI are documented here. This project follows
[Keep a Changelog](https://keepachangelog.com/) and
[Semantic Versioning](https://semver.org/).

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
