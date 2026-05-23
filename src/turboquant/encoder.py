import struct
from dataclasses import dataclass

import numpy as np

from .matrices import get_rotation_matrix, get_qjl_matrix, DIM
from . import polar_quant, qjl

BLOB_SIZE = 58  # 2 (radius float16) + 48 (angle bits) + 8 (QJL bits)


@dataclass
class CompressedVector:
    radius: np.float16       # 2 bytes
    angle_bits: np.ndarray   # shape (48,) uint8
    qjl_bits: np.ndarray     # shape (8,)  uint8
    residual_norm: float     # stored in SQLite, NOT in the 58-byte blob


def encode(v: np.ndarray) -> CompressedVector:
    R = get_rotation_matrix()
    G = get_qjl_matrix()

    radius, packed_angles = polar_quant.encode(v, R)
    v_hat = polar_quant.decode(radius, packed_angles, R)

    residual = v.astype(np.float32) - v_hat
    packed_qjl, residual_norm = qjl.encode_residual(residual, G)

    return CompressedVector(
        radius=radius,
        angle_bits=packed_angles,
        qjl_bits=packed_qjl,
        residual_norm=residual_norm,
    )


def decode(cv: CompressedVector) -> np.ndarray:
    R = get_rotation_matrix()
    return polar_quant.decode(cv.radius, cv.angle_bits, R)


def to_bytes(cv: CompressedVector) -> bytes:
    """Serialize to exactly 58 bytes."""
    radius_bytes = struct.pack("e", float(cv.radius))  # 2 bytes, float16
    return radius_bytes + cv.angle_bits.tobytes() + cv.qjl_bits.tobytes()


def from_bytes(blob: bytes, residual_norm: float = 0.0) -> CompressedVector:
    """Deserialize 58-byte blob back to CompressedVector."""
    assert len(blob) == BLOB_SIZE, f"Expected {BLOB_SIZE} bytes, got {len(blob)}"
    radius = np.float16(struct.unpack("e", blob[:2])[0])
    angle_bits = np.frombuffer(blob[2:50], dtype=np.uint8).copy()
    qjl_bits = np.frombuffer(blob[50:58], dtype=np.uint8).copy()
    return CompressedVector(radius=radius, angle_bits=angle_bits, qjl_bits=qjl_bits, residual_norm=residual_norm)


def compressed_dot_product(a: CompressedVector, b: CompressedVector) -> float:
    """Approximate <v_a, v_b> without full decompression."""
    R = get_rotation_matrix()
    v_a_hat = polar_quant.decode(a.radius, a.angle_bits, R)
    v_b_hat = polar_quant.decode(b.radius, b.angle_bits, R)
    stage1_dot = float(np.dot(v_a_hat, v_b_hat))

    signs_a = qjl.unpack_signs(a.qjl_bits)
    signs_b = qjl.unpack_signs(b.qjl_bits)
    residual_correction = qjl.residual_dot_product(signs_a, signs_b, a.residual_norm, b.residual_norm)

    return stage1_dot + residual_correction


def batch_decode_angles(cvs: list[CompressedVector]) -> np.ndarray:
    """Decode Stage 1 for all CVs into (N, DIM) float32 matrix for fast batch search."""
    R = get_rotation_matrix()
    matrix = np.empty((len(cvs), DIM), dtype=np.float32)
    for i, cv in enumerate(cvs):
        matrix[i] = polar_quant.decode(cv.radius, cv.angle_bits, R)
    return matrix


def batch_dot_products(query_cv: CompressedVector, all_cvs: list[CompressedVector]) -> np.ndarray:
    """
    Returns (N,) float32 score array.
    Decodes all stored angle_bits into a matrix then does a single matmul —
    faster than N individual compressed_dot_product calls for large N.
    """
    if not all_cvs:
        return np.empty(0, dtype=np.float32)

    R = get_rotation_matrix()
    query_hat = polar_quant.decode(query_cv.radius, query_cv.angle_bits, R)

    stored_matrix = batch_decode_angles(all_cvs)  # (N, DIM)
    stage1_scores = stored_matrix @ query_hat      # (N,)

    # QJL residual correction for each stored vector
    query_signs = qjl.unpack_signs(query_cv.qjl_bits)
    residual_corrections = np.array([
        qjl.residual_dot_product(qjl.unpack_signs(cv.qjl_bits), query_signs, cv.residual_norm, query_cv.residual_norm)
        for cv in all_cvs
    ], dtype=np.float32)

    return (stage1_scores + residual_corrections).astype(np.float32)
