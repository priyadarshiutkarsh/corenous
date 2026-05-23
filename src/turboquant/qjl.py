import numpy as np
from .matrices import get_qjl_matrix, QJL_K


def encode_residual(residual: np.ndarray, G: np.ndarray) -> tuple[np.ndarray, float]:
    """Project residual via G, keep sign bits. Returns (packed_8_bytes, residual_norm)."""
    residual_norm = float(np.linalg.norm(residual))
    projected = G @ residual  # shape (QJL_K,)
    sign_bits = (projected > 0).astype(np.uint8)
    packed = np.packbits(sign_bits, bitorder="big")  # shape (8,) uint8
    return packed, residual_norm


def unpack_signs(packed: np.ndarray) -> np.ndarray:
    """(8,) uint8 → (64,) int8 values in {-1, +1}."""
    bits = np.unpackbits(packed, bitorder="big").astype(np.int8)
    return np.where(bits == 1, np.int8(1), np.int8(-1))


def residual_dot_product(
    signs_a: np.ndarray,
    signs_b: np.ndarray,
    norm_a: float,
    norm_b: float,
) -> float:
    """Estimate <residual_a, residual_b> from sign bits using the JL angle estimator."""
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    agreement = int(np.sum(signs_a == signs_b))
    theta_est = np.pi * (1.0 - agreement / QJL_K)
    return norm_a * norm_b * float(np.cos(theta_est))
