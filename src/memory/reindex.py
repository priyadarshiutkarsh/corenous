"""Re-embedding and embedding-model drift detection.

Switching embedding models (e.g. all-MiniLM-L6-v2 -> bge-small-en-v1.5) puts new
query vectors in a different space than the stored ones, so search silently
degrades for existing memories until they are re-encoded. We record the model
name in the store's config and warn when it no longer matches, and reindex_all
re-encodes everything with the current model.
"""
from __future__ import annotations

import numpy as np

from .embedder import Embedder, _MODEL_NAME
from ..turboquant import encoder as tq

_CONFIG_KEY = "embed_model"


def reindex_all(store) -> int:
    """Re-embed every non-sensitive memory with the current model and record the
    model name. Returns the number of memories re-encoded."""
    emb = Embedder.get()
    rows = store._conn.execute(
        "SELECT m.id, m.full_text, m.text_snippet FROM memories m "
        "JOIN vectors v ON v.memory_id = m.id WHERE m.is_sensitive = 0"
    ).fetchall()
    done = 0
    for r in rows:
        text = (r["full_text"] or r["text_snippet"] or "").strip()
        if not text:
            continue
        vec = emb.embed(text)                       # document mode
        cv = tq.encode(vec)
        store.update_memory_vector(
            r["id"], cv, cv.residual_norm, fp16=vec.astype(np.float16).tobytes(),
        )
        done += 1
    store.set_config(_CONFIG_KEY, _MODEL_NAME)
    return done


def model_mismatch_warning(store) -> str | None:
    """Return a one-line warning if the store's vectors were built with a
    different embedding model than the current one, else None. Records the
    current model on a fresh (empty) store so it is tracked going forward."""
    try:
        stored = store.get_config(_CONFIG_KEY, "")
        n = store.get_memory_count()
    except Exception:
        return None
    if not stored:
        if n == 0:
            try:
                store.set_config(_CONFIG_KEY, _MODEL_NAME)
            except Exception:
                pass
            return None
        return ("Existing memories have no embedding-model record. If you "
                "upgraded Corenous, run `corenous-ai reindex` so search works "
                "on them.")
    if stored != _MODEL_NAME:
        return (f"Embedding model changed ({stored} -> {_MODEL_NAME}); run "
                f"`corenous-ai reindex` to re-encode {n} memories, or search on "
                f"older memories will be degraded.")
    return None
