"""Cross-encoder reranker.

The embedder is a bi-encoder: it scores the query and a document independently,
so it never sees them together. A cross-encoder reads the (query, document) pair
jointly and judges relevance directly, which ranks far better, at the cost of one
model forward pass per candidate. We use it only as a final rerank over the few
dozen top fused candidates, so the cost stays bounded.

Measured on LoCoMo, reranking roughly doubles MRR over the fused ranking alone.
Upgrading the cross-encoder from the 2-layer TinyBERT to this 4-layer MiniLM
added a further +3.7 recall@10 / +4.9 recall@5 / +0.047 MRR at the same speed.
The model is lazy-loaded so nothing pays for it until the first rerank.
"""
from __future__ import annotations

import numpy as np

# ms-marco-MiniLM-L-4-v2: a 4-layer cross-encoder that runs correctly on CPU.
# The 6-layer variant returns nan on CPU under torch 2.11 (verified), so it is
# off the table; the 4-layer model is the strongest that works without MPS (and
# thus without the Metal crash risk). At ~7ms/pair it is the same speed class as
# the old 2-layer TinyBERT, so a rerank of a few dozen candidates stays well
# under a second, while the extra layers improve ranking quality.
_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-4-v2"
_model = None


def _load():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder
        _model = CrossEncoder(_MODEL_NAME, device="cpu")
    return _model


def rerank_scores(query: str, docs: list[str]) -> np.ndarray:
    """Relevance score per document for ``query`` (higher is better). Empty in,
    empty out. Loads the cross-encoder on first call."""
    if not docs:
        return np.empty(0, dtype=np.float32)
    pairs = [(query, d) for d in docs]
    return np.asarray(_load().predict(pairs), dtype=np.float32)


def is_loaded() -> bool:
    return _model is not None
