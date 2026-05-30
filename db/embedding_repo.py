"""Repository helpers for capture identity and section embeddings."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from agents.embedder import cosine_similarity


class CaptureEmbeddingRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(
        self,
        *,
        capture_id: str,
        kind: str,
        section_id: str = "",
        section_title: str = "",
        index_text: str,
        embedding: bytes,
        source_hash: str,
        file_path: str,
    ) -> dict:
        row_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO capture_embeddings
               (id, capture_id, kind, section_id, section_title, index_text,
                embedding, source_hash, file_path, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(capture_id, kind, section_id) DO UPDATE SET
                 section_title=excluded.section_title,
                 index_text=excluded.index_text,
                 embedding=excluded.embedding,
                 source_hash=excluded.source_hash,
                 file_path=excluded.file_path,
                 updated_at=excluded.updated_at""",
            (
                row_id,
                capture_id,
                kind,
                section_id,
                section_title,
                index_text,
                embedding,
                source_hash,
                file_path,
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get(capture_id, kind, section_id) or {}

    def get(self, capture_id: str, kind: str, section_id: str = "") -> dict | None:
        row = self.conn.execute(
            """SELECT * FROM capture_embeddings
               WHERE capture_id = ? AND kind = ? AND section_id = ?""",
            (capture_id, kind, section_id),
        ).fetchone()
        return dict(row) if row else None

    def list_by_capture(self, capture_id: str) -> list[dict]:
        rows = self.conn.execute(
            """SELECT * FROM capture_embeddings
               WHERE capture_id = ?
               ORDER BY kind ASC, section_id ASC""",
            (capture_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def needs_reindex(
        self,
        capture_id: str,
        kind: str,
        section_id: str,
        source_hash: str,
    ) -> bool:
        row = self.get(capture_id, kind, section_id)
        return not row or row.get("source_hash") != source_hash or not row.get("embedding")

    def search(
        self,
        query_embedding: bytes,
        *,
        kinds: tuple[str, ...] = ("identity", "section"),
        limit: int = 5,
    ) -> list[dict]:
        placeholders = ", ".join("?" for _ in kinds)
        rows = self.conn.execute(
            f"""SELECT * FROM capture_embeddings
                WHERE embedding IS NOT NULL AND kind IN ({placeholders})""",
            kinds,
        ).fetchall()
        scored: list[dict] = []
        for row in rows:
            item = dict(row)
            item["score"] = cosine_similarity(query_embedding, item["embedding"])
            scored.append(item)
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:limit]
