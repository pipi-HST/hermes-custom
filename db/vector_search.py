"""向量检索——cosine 相似度 top-k."""
from __future__ import annotations

from db.repo import CaptureRepo
from agents.embedder import Embedder, batch_cosine_similarity


def search(query: str, repo: CaptureRepo, embedder: Embedder,
           top_k: int = 5, *, query_embedding: bytes | None = None) -> list[dict]:
    """检索 top-k 最相关的 captures（按 file_path 去重）。

    ``query_embedding`` lets a caller that already embedded the query (the RAG
    bridge does) reuse it instead of paying for a second API round trip.
    """
    query_emb = query_embedding if query_embedding is not None else embedder.embed(query)
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

    # Return in score order with the same routing fields as the indexed path.
    # The graph expander drops seeds without ``matched_kind`` and ranks by
    # ``score``; returning bare capture rows made every fallback seed look like
    # a 0-score, unroutable hit.
    results: list[dict] = []
    for cid, score in scored:
        if cid not in top_ids:
            continue
        item = dict(id_to_row[cid])
        item["score"] = float(score)
        item["matched_kind"] = "identity"
        item["section_id"] = ""
        item.setdefault("index_text", str(item.get("title") or ""))
        item["vector_source"] = "capture_summary"
        results.append(item)
    return results


def _list_embedding_captures(repo: CaptureRepo) -> list[dict]:
    rows = repo.conn.execute(
        """SELECT *
           FROM captures
           WHERE embedding IS NOT NULL
             AND COALESCE(review_state, '') != 'archived'
           ORDER BY created_at DESC"""
    ).fetchall()
    return [dict(row) for row in rows]
