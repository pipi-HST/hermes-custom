"""千问 text-embedding-v4 封装 + cosine 相似度计算."""
from __future__ import annotations

import math
import struct
from typing import Optional

import httpx

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


def cosine_similarity(emb1: bytes, emb2: bytes) -> float:
    v1 = struct.unpack(f"{DIM}f", emb1[:DIM * 4])
    v2 = struct.unpack(f"{DIM}f", emb2[:DIM * 4])
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def find_top_k(query_emb: bytes, candidates: list[tuple[str, bytes]],
               k: int = 15) -> list[tuple[str, float]]:
    scored = [(cid, cosine_similarity(query_emb, emb)) for cid, emb in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]
