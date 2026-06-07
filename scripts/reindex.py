#!/usr/bin/env python3
"""Re-embed every stored memory with the current embedding model.

Thin wrapper around src.memory.reindex.reindex_all. Prefer the CLI command
`corenous-ai reindex`; this exists for running straight from a checkout.

Run:  ./.venv/bin/python scripts/reindex.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.cli.context import AppContext
from src.memory.reindex import reindex_all


def main() -> None:
    store = AppContext.load(Path.cwd()).store
    print("re-embedding memories with the current model ...", flush=True)
    n = reindex_all(store)
    print(f"reindexed {n} memories. Restart the daemon to reload the cache:", flush=True)
    print("  corenous-ai daemon stop && corenous-ai daemon start", flush=True)


if __name__ == "__main__":
    main()
