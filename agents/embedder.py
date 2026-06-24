"""千问 text-embedding-v4 封装 + numpy 向量化 cosine 相似度计算."""
from __future__ import annotations

import struct
from typing import Optional

import httpx
import numpy as np

DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "text-embedding-v4"
DIM = 1024


class Embedder:
    def __init__(self, api_key: str, base_url: Optional[str] = None):
        self.api_key = api_key
        self.base_url = base_url or DASHSCOPE_BASE

    def embed(self, text: str) -> bytes:
        """返回 1024-dim float32 bytes (4096 bytes)."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": MODEL,
            "input": text,
        }
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{self.base_url}/embeddings",
                headers=headers, json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            vec = data["data"][0]["embedding"]
            return struct.pack(f"{DIM}f", *vec)


def _bytes_to_matrix(embeddings: list[bytes]) -> np.ndarray:
    """Convert list of 4096-byte embedding blobs to (N, DIM) float32 matrix."""
    if not embeddings:
        return np.empty((0, DIM), dtype=np.float32)
    raw = np.frombuffer(b"".join(embeddings), dtype=np.float32)
    return raw.reshape(len(embeddings), DIM)


def _normalize(matrix: np.ndarray) -> np.ndarray:
    """L2-normalize rows in-place. Zero-norm rows stay zero."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    safe_norms = np.where(norms == 0, 1.0, norms)
    return matrix / safe_norms


def batch_cosine_similarity(
    query_embedding: bytes,
    embeddings: list[bytes],
) -> list[float]:
    """Vectorized cosine similarity over all candidates. ~1000x faster than scalar loop."""
    if not embeddings:
        return []
    matrix = _normalize(_bytes_to_matrix(embeddings))
    query = _normalize(_bytes_to_matrix([query_embedding]))[0]
    return (matrix @ query).tolist()


def cosine_similarity(emb1: bytes, emb2: bytes) -> float:
    v1 = np.frombuffer(emb1[: DIM * 4], dtype=np.float32)
    v2 = np.frombuffer(emb2[: DIM * 4], dtype=np.float32)
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 == 0 or n2 == 0:
        return 0.0
    return float(np.dot(v1, v2) / (n1 * n2))


def find_top_k(query_emb: bytes, candidates: list[tuple[str, bytes]],
               k: int = 15) -> list[tuple[str, float]]:
    if not candidates:
        return []
    ids = [c[0] for c in candidates]
    scores = batch_cosine_similarity(query_emb, [c[1] for c in candidates])
    scored = sorted(zip(ids, scores), key=lambda x: x[1], reverse=True)
    return scored[:k]
