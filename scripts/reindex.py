#!/usr/bin/env python3
"""Re-embed every stored memory with the CURRENT embedding model.

Switching embedding models (e.g. all-MiniLM-L6-v2 -> bge-small-en-v1.5) puts new
query vectors in a different space than the stored ones, so existing memories
must be re-encoded once or search quietly degrades for them. Run after changing
the model, then restart the daemon so the in-RAM cache reloads.

Run:  ./.venv/bin/python scripts/reindex.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.cli.context import AppContext
from src.memory.embedder import Embedder
from src.turboquant import encoder as tq


def main() -> None:
    app = AppContext.load(Path.cwd())
    store = app.store
    emb = Embedder()
    rows = store._conn.execute(
        "SELECT m.id, m.full_text, m.text_snippet FROM memories m "
        "JOIN vectors v ON v.memory_id = m.id WHERE m.is_sensitive = 0"
    ).fetchall()
    print(f"re-embedding {len(rows)} memories with the current model ...", flush=True)
    done = 0
    for r in rows:
        text = (r["full_text"] or r["text_snippet"] or "").strip()
        if not text:
            continue
        vec = emb.embed(text)                       # document mode (no query prefix)
        cv = tq.encode(vec)
        store.update_memory_vector(
            r["id"], cv, cv.residual_norm,
            fp16=vec.astype(np.float16).tobytes(),
        )
        done += 1
    print(f"reindexed {done} memories. Restart the daemon to reload the cache:", flush=True)
    print("  corenous-ai daemon stop && corenous-ai daemon start", flush=True)


if __name__ == "__main__":
    main()
