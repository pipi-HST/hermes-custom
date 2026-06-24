"""向量检索——cosine 相似度 top-k."""
from __future__ import annotations

from db.repo import CaptureRepo
from agents.embedder import Embedder, batch_cosine_similarity


def search(query: str, repo: CaptureRepo, embedder: Embedder,
           top_k: int = 5) -> list[dict]:
    """检索 top-k 最相关的 captures（按 file_path 去重）。"""
    query_emb = embedder.embed(query)
    embedding_captures = _list_embedding_captures(repo)
    candidates = [(c["id"], c.get("embedding")) for c in embedding_captures
                  if c.get("embedding")]
    if not candidates:
        return []

    ids = [c[0] for c in candidates]
    embs = [c[1] for c in candidates]
    scores = batch_cosine_similarity(query_emb, embs)
    scored = sorted(zip(ids, scores), key=lambda x: x[1], reverse=True)

    # 按 file_path 去重：同一文件只保留得分最高的
    seen_paths: set[str] = set()
    top_ids: set[str] = set()
    id_to_row = {c["id"]: c for c in embedding_captures}
    for cid, _ in scored:
        fp = id_to_row[cid].get("file_path") or ""
        if fp and fp in seen_paths:
            continue
        if fp:
            seen_paths.add(fp)
        top_ids.add(cid)
        if len(top_ids) >= top_k:
            break

    return [c for c in embedding_captures if c["id"] in top_ids]


def _list_embedding_captures(repo: CaptureRepo) -> list[dict]:
    rows = repo.conn.execute(
        """SELECT *
           FROM captures
           WHERE embedding IS NOT NULL
           ORDER BY created_at DESC"""
    ).fetchall()
    return [dict(row) for row in rows]
