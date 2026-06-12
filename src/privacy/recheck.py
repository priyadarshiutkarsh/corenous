"""Deferred re-check for the AI sensitivity layer.

The capture path's contextual AI check (layer 4 of SensitivityDetector) is
non-blocking: when the local model is busy the capture is stored unchecked.
This module gives those captures a second pass once the model is free, so
"model was busy" can never silently become "sensitive content stays in the
plain store forever". The daemon drives this from a background loop.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..memory.store import MemoryStore
    from ..memory.vector_cache import VectorCache
    from .vault import Vault


def recheck_sensitivity(
    store: "MemoryStore",
    cache: "VectorCache",
    vault: "Vault",
    memory_id: int,
    classify_fn=None,
) -> str:
    """Re-run the contextual AI sensitivity check for an already-stored memory.

    Returns one of:
      'deferred' — model still busy; caller should retry later
      'clean'    — model ran and found nothing sensitive
      'vaulted'  — sensitive; encrypted into the vault and removed from the
                   plain store
      'dropped'  — sensitive but the vault is locked or uninitialized; removed
                   from the plain store anyway (same privacy posture as the
                   capture path, which never keeps flagged plaintext)
      'gone'     — memory no longer exists (deleted meanwhile)

    ``classify_fn`` defaults to ``ai_is_sensitive``; injectable so tests can
    pass a deterministic stub.
    """
    row = store.get_memory_by_id(int(memory_id))
    if not row:
        return "gone"
    text = (row.get("full_text") or row.get("text_snippet") or "").strip()
    if not text:
        return "clean"
    if classify_fn is None:
        from ..ai.summarizer import ai_is_sensitive as classify_fn
    verdict, reason = classify_fn(text)
    if verdict is None:
        return "deferred"
    if not verdict:
        return "clean"
    if vault.can_seal():
        vault.seal(
            text,
            row.get("source") or "",
            row.get("app_name") or "",
            [f"ai_context:{reason}"],
            float(row.get("created_at") or 0.0),
        )
        outcome = "vaulted"
    else:
        outcome = "dropped"
    store.delete_memory(int(memory_id))
    cache.remove(int(memory_id))
    return outcome
