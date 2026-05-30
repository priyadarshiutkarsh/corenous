"""In-memory NumPy cache of compressed vectors, rebuilt from the store at startup."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..turboquant import qjl
from ..turboquant.encoder import (
    CompressedVector,
    batch_decode_angles,
    decode,
)


class VectorCache:
    def __init__(self, cache_path: Path) -> None:
        self._path = cache_path
        self._memory_ids: list[int] = []
        self._residual_norms: list[float] = []
        self._cvs: list[CompressedVector] = []
        self._stage1_matrix: np.ndarray | None = None
        self._qjl_signs: np.ndarray | None = None
        self._residual_norms_np: np.ndarray = np.empty(0, dtype=np.float32)

    def clear(self) -> None:
        """Drop all cached vectors (e.g. after wiping the database)."""
        self.load_from_store([])

    def load_from_store(self, entries: list[tuple[int, CompressedVector, float]]) -> None:
        """Populate cache from MemoryStore.get_all_compressed_vectors() output."""
        self._memory_ids = [e[0] for e in entries]
        self._cvs = [e[1] for e in entries]
        self._residual_norms = [e[2] for e in entries]
        self._rebuild_fast_arrays()

    def append(self, memory_id: int, cv: CompressedVector, residual_norm: float) -> None:
        """Append compressed TurboQuant rows — matrices rebuild lazily on ``scores`` (O(n) once).

        Incremental ``np.vstack`` per capture was copying the full matrix each time
        (quadratic CPU as memories grow); batch ``batch_decode_angles`` stays linear.
        """
        self._memory_ids.append(memory_id)
        self._cvs.append(cv)
        self._residual_norms.append(residual_norm)
        self._stage1_matrix = None
        self._qjl_signs = None
        self._residual_norms_np = np.asarray(self._residual_norms, dtype=np.float32)

    def remove(self, memory_id: int) -> bool:
        """Drop the cached vector for ``memory_id`` so deletes don't ghost in
        search results. Returns True if the entry was found and removed."""
        try:
            idx = self._memory_ids.index(int(memory_id))
        except ValueError:
            return False
        del self._memory_ids[idx]
        del self._cvs[idx]
        del self._residual_norms[idx]
        # Force the fast-path arrays to rebuild on the next ``scores`` call.
        self._stage1_matrix = None
        self._qjl_signs = None
        self._residual_norms_np = np.asarray(self._residual_norms, dtype=np.float32)
        return True

    def replace(self, memory_id: int, cv: CompressedVector, residual_norm: float) -> bool:
        """Replace the cached vector for an existing memory id."""
        try:
            idx = self._memory_ids.index(int(memory_id))
        except ValueError:
            return False
        self._cvs[idx] = cv
        self._residual_norms[idx] = float(residual_norm)
        self._stage1_matrix = None
        self._qjl_signs = None
        self._residual_norms_np = np.asarray(self._residual_norms, dtype=np.float32)
        return True

    def get_all(self) -> list[tuple[int, CompressedVector]]:
        return list(zip(self._memory_ids, self._cvs))

    def scores(self, query_cv: CompressedVector) -> np.ndarray:
        """Fast TurboQuant scores against every cached memory."""
        if not self._memory_ids:
            return np.empty(0, dtype=np.float32)
        if self._stage1_matrix is None or self._qjl_signs is None:
            self._rebuild_fast_arrays()
        query_hat = decode(query_cv)
        stage1 = self._stage1_matrix @ query_hat

        query_signs = qjl.unpack_signs(query_cv.qjl_bits)
        agreements = np.sum(self._qjl_signs == query_signs, axis=1)
        theta = np.pi * (1.0 - agreements / self._qjl_signs.shape[1])
        residual = (
            self._residual_norms_np
            * float(query_cv.residual_norm)
            * np.cos(theta)
        )
        return (stage1 + residual).astype(np.float32)

    def memory_ids(self) -> list[int]:
        return list(self._memory_ids)

    def __len__(self) -> int:
        return len(self._memory_ids)

    def _rebuild_fast_arrays(self) -> None:
        if not self._cvs:
            self._stage1_matrix = None
            self._qjl_signs = None
            self._residual_norms_np = np.empty(0, dtype=np.float32)
            return
        self._stage1_matrix = batch_decode_angles(self._cvs)
        self._qjl_signs = np.vstack([
            qjl.unpack_signs(cv.qjl_bits) for cv in self._cvs
        ])
        self._residual_norms_np = np.asarray(self._residual_norms, dtype=np.float32)
