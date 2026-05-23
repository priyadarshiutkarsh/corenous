import numpy as np

DIM = 384
QJL_K = 64
ROTATION_SEED = 0xC0DE_0001  # 3,220,832,257
QJL_SEED      = 0xC0DE_0002  # 3,220,832,258

_R: np.ndarray | None = None
_G: np.ndarray | None = None


def get_rotation_matrix() -> np.ndarray:
    global _R
    if _R is None:
        rng = np.random.default_rng(ROTATION_SEED)
        M = rng.standard_normal((DIM, DIM)).astype(np.float32)
        _R, _ = np.linalg.qr(M)
    return _R


def get_qjl_matrix() -> np.ndarray:
    global _G
    if _G is None:
        rng = np.random.default_rng(QJL_SEED)
        _G = rng.standard_normal((QJL_K, DIM)).astype(np.float32) / np.sqrt(DIM)
    return _G
