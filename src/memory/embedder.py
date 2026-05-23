"""Local embedding using sentence-transformers (all-MiniLM-L6-v2, 384-dim, Apache 2.0)."""
from __future__ import annotations

import numpy as np

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_MAX_TOKENS  = 256


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
            self._model = SentenceTransformer(_MODEL_NAME)

    def embed(self, text: str) -> np.ndarray:
        """Return (384,) float32 unit vector. Model is lazy-loaded on first call."""
        self._load()
        vec = self._model.encode(
            text,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vec.astype(np.float32)

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        """Return (N, 384) float32 array of unit vectors."""
        self._load()
        vecs = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=64,
        )
        return vecs.astype(np.float32)

    def is_loaded(self) -> bool:
        return self._model is not None
