"""Cross-encoder reranker.

The embedder is a bi-encoder: it scores the query and a document independently,
so it never sees them together. A cross-encoder reads the (query, document) pair
jointly and judges relevance directly, which ranks far better, at the cost of one
model forward pass per candidate. We use it only as a final rerank over the few
dozen top fused candidates, so the cost stays bounded.

Measured on LoCoMo it roughly doubled MRR (0.28 to 0.50) and lifted recall@10 by
about 11 points over the fused ranking alone. The model is lazy-loaded so nothing
pays for it until the first rerank.
"""
from __future__ import annotations

import numpy as np

# ms-marco-TinyBERT-L-2-v2: a 2-layer cross-encoder that runs correctly on CPU
# (the larger MiniLM-L-6 variant returns nan on CPU under torch 2.11, and only
# works on MPS, which would reintroduce the Metal crash risk). Tiny and fast, so
# a deep-search rerank of a few dozen candidates stays well under a second.
_MODEL_NAME = "cross-encoder/ms-marco-TinyBERT-L-2-v2"
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
