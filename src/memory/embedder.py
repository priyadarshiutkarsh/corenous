"""Local embedding using sentence-transformers (bge-small-en-v1.5, 384-dim, MIT)."""
from __future__ import annotations

import numpy as np

_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_MAX_TOKENS  = 256
# bge-v1.5 is asymmetric: prepend this instruction to QUERIES only (not to the
# stored documents). Measured a large retrieval gain over all-MiniLM-L6-v2 while
# staying 384-dim, so TurboQuant is unchanged.
_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder:
    _instance: "Embedder | None" = None

    def __init__(self) -> None:
        self._model = None  # lazy-load on first use

    @classmethod
    def get(cls) -> "Embedder":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load(self) -> None:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            # Pin to CPU. On MPS the per-capture embed competes with the VL model
            # for the GPU and can hit a Metal command-buffer timeout, which raises
            # an uncatchable C++ std::terminate that kills the whole daemon. MiniLM
            # on CPU embeds one short capture in a few ms, off the critical path,
            # so this removes the most frequent crash vector at no real cost.
            self._model = SentenceTransformer(_MODEL_NAME, device="cpu")

    def embed(self, text: str, is_query: bool = False) -> np.ndarray:
        """Return (384,) float32 unit vector. Model is lazy-loaded on first call.
        Pass ``is_query=True`` when embedding a search query (adds the bge query
        instruction); leave it False for stored captures/documents."""
        self._load()
        if is_query:
            text = _QUERY_PREFIX + text
        vec = self._model.encode(
            text,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vec.astype(np.float32)

    def embed_batch(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        """Return (N, 384) float32 array of unit vectors."""
        self._load()
        if is_query:
            texts = [_QUERY_PREFIX + t for t in texts]
        vecs = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=64,
        )
        return vecs.astype(np.float32)

    def is_loaded(self) -> bool:
        return self._model is not None
