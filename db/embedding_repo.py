"""Repository helpers for capture identity and section embeddings."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from agents.embedder import batch_cosine_similarity
from db.fts_tokenizer import tokenize_for_fts


def _delete_fts_row(
    conn: sqlite3.Connection,
    *,
    capture_id: str,
    kind: str,
    section_id: str,
) -> None:
    """Keep the derived FTS index in step with ``capture_embeddings``.

    Silently skipped when the database was built without FTS5; the keyword
    search path then degrades to its legacy implementation.
    """
    try:
        conn.execute(
            "DELETE FROM capture_search_fts "
            "WHERE capture_id = ? AND kind = ? AND section_id = ?",
            (capture_id, kind, section_id),
        )
    except sqlite3.OperationalError:
        pass


def _insert_fts_row(
    conn: sqlite3.Connection,
    *,
    capture_id: str,
    kind: str,
    section_id: str,
    section_title: str,
    index_text: str,
) -> None:
    try:
        conn.execute(
            "INSERT INTO capture_search_fts "
            "(capture_id, kind, section_id, section_title_tokens, index_text_tokens) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                capture_id,
                kind,
                section_id,
                tokenize_for_fts(section_title),
                tokenize_for_fts(index_text),
            ),
        )
    except sqlite3.OperationalError:
        pass


def rebuild_fts_from_embeddings(conn: sqlite3.Connection) -> int:
    """Rebuild the whole keyword index from ``capture_embeddings``.

    The FTS table is derived data.  A plain re-index run skips chunks whose
    ``source_hash`` is unchanged, so a database that predates the FTS table
    would otherwise keep an empty index forever and keyword search would look
    broken for no visible reason.
    """
    from db.schema import CREATE_CAPTURE_SEARCH_FTS_TABLE

    try:
        conn.execute(CREATE_CAPTURE_SEARCH_FTS_TABLE)
        conn.execute("DELETE FROM capture_search_fts")
    except sqlite3.OperationalError:
        return 0
    rows = conn.execute(
        "SELECT capture_id, kind, section_id, section_title, index_text "
        "FROM capture_embeddings"
    ).fetchall()
    payload = [
        (
            str(row["capture_id"] or ""),
            str(row["kind"] or ""),
            str(row["section_id"] or ""),
            tokenize_for_fts(str(row["section_title"] or "")),
            tokenize_for_fts(str(row["index_text"] or "")),
        )
        for row in rows
    ]
    if payload:
        conn.executemany(
            "INSERT INTO capture_search_fts "
            "(capture_id, kind, section_id, section_title_tokens, index_text_tokens) "
            "VALUES (?, ?, ?, ?, ?)",
            payload,
        )
    conn.commit()
    return len(payload)


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
        _delete_fts_row(
            self.conn, capture_id=capture_id, kind=kind, section_id=section_id
        )
        _insert_fts_row(
            self.conn,
            capture_id=capture_id,
            kind=kind,
            section_id=section_id,
            section_title=section_title,
            index_text=index_text,
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

    def delete_missing_for_capture(
        self,
        capture_id: str,
        current_keys: set[tuple[str, str]],
    ) -> int:
        """Remove embedding rows that no longer correspond to the current note."""
        rows = self.conn.execute(
            """SELECT kind, section_id FROM capture_embeddings
               WHERE capture_id = ?""",
            (capture_id,),
        ).fetchall()
        stale_keys = [
            (row["kind"], row["section_id"])
            for row in rows
            if (row["kind"], row["section_id"]) not in current_keys
        ]
        if not stale_keys:
            return 0
        self.conn.executemany(
            """DELETE FROM capture_embeddings
               WHERE capture_id = ? AND kind = ? AND section_id = ?""",
            [(capture_id, kind, section_id) for kind, section_id in stale_keys],
        )
        for kind, section_id in stale_keys:
            _delete_fts_row(
                self.conn,
                capture_id=capture_id,
                kind=kind,
                section_id=section_id,
            )
        self.conn.commit()
        return len(stale_keys)

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
        two_stage_threshold: int = 40,
    ) -> list[dict]:
        if not kinds or limit <= 0:
            return []
        placeholders = ", ".join("?" for _ in kinds)
        rows = self.conn.execute(
            f"""SELECT ce.id, ce.capture_id, ce.kind, ce.embedding
                FROM capture_embeddings ce
                JOIN captures c ON c.id = ce.capture_id
                WHERE ce.embedding IS NOT NULL
                  AND ce.kind IN ({placeholders})
                  AND COALESCE(c.review_state, '') != 'archived'""",
            kinds,
        ).fetchall()
        if not rows:
            return []
        if len(rows) > two_stage_threshold and "section" in kinds:
            rows = self._coarse_filter(query_embedding, rows)
        embeddings = [row["embedding"] for row in rows]
        scores = batch_cosine_similarity(query_embedding, embeddings)
        scored: list[tuple[str, float]] = []
        for row, score in zip(rows, scores):
            scored.append((row["id"], score))
        scored.sort(key=lambda item: item[1], reverse=True)

        top_scored = scored[:limit]
        top_ids = [row_id for row_id, _score in top_scored]
        top_scores = {row_id: score for row_id, score in top_scored}
        full_rows = self._get_rows_by_ids(top_ids)
        full_rows_by_id = {row["id"]: dict(row) for row in full_rows}

        items: list[dict] = []
        for row_id in top_ids:
            item = full_rows_by_id.get(row_id)
            if not item:
                continue
            item["score"] = top_scores[row_id]
            items.append(item)
        return items

    @staticmethod
    def _coarse_filter(query_embedding: bytes, rows: list, *, keep: int = 30) -> list:
        """Stage 1: rank whole notes by identity, then score only their sections.

        A full scan already happened to load the rows; the saving is cosine work
        and the row hydration that used to run over every section of every note.
        """
        from agents.embedder import batch_cosine_similarity as _cos

        identity_rows = [row for row in rows if row["kind"] == "identity"]
        if not identity_rows:
            return rows
        scores = _cos(query_embedding, [row["embedding"] for row in identity_rows])
        ranked = sorted(
            zip(identity_rows, scores), key=lambda pair: pair[1], reverse=True
        )
        keep_ids = {
            str(row["capture_id"])
            for row, _score in ranked[:keep]
        }
        # A note that only has section rows cannot be ranked; keep it rather
        # than silently dropping its evidence.
        identity_capture_ids = {str(row["capture_id"]) for row in identity_rows}
        for row in rows:
            capture_id = str(row["capture_id"])
            if capture_id not in identity_capture_ids:
                keep_ids.add(capture_id)
        if not keep_ids:
            return rows
        return [row for row in rows if str(row["capture_id"]) in keep_ids]

    def _get_rows_by_ids(self, row_ids: list[str]) -> list[sqlite3.Row]:
        if not row_ids:
            return []
        placeholders = ", ".join("?" for _ in row_ids)
        return self.conn.execute(
            f"""SELECT * FROM capture_embeddings
                WHERE id IN ({placeholders})""",
            row_ids,
        ).fetchall()
