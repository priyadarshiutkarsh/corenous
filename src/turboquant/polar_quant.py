"""
Stage 1 of TurboQuant: PolarQuant.

After a random orthonormal rotation (which spreads information uniformly across
all dimensions), we separate the radius from the direction, then 1-bit sign-quantize
the direction.  The random rotation ensures sign bits carry maximum information —
each bit independently captures one bit of the original signal rather than being
concentrated in the first few components.
"""
import numpy as np
from .matrices import DIM


def encode(v: np.ndarray, R: np.ndarray) -> tuple[np.float16, np.ndarray]:
    """Rotate → separate radius → sign-quantize direction → 48 packed bytes."""
    v_rot = (R @ v).astype(np.float64)
    radius = np.float16(np.linalg.norm(v_rot))

    unit = v_rot / float(radius)
    bits = (unit >= 0).astype(np.uint8)          # shape (384,)
    packed = np.packbits(bits, bitorder="big")   # shape (48,) uint8
    return radius, packed


def decode(radius: np.float16, packed: np.ndarray, R: np.ndarray) -> np.ndarray:
    """Unpack sign bits → reconstruct approximate unit vector → back-rotate."""
    bits = np.unpackbits(packed, bitorder="big").astype(np.float32)  # (384,)
    signs = 2.0 * bits - 1.0                      # map 0→-1, 1→+1

    # Normalize to unit sphere (each sign is ±1, so norm = sqrt(DIM))
    unit_hat = (signs / np.sqrt(DIM)).astype(np.float32)

    v_hat_rot = float(radius) * unit_hat
    return (R.T @ v_hat_rot).astype(np.float32)
