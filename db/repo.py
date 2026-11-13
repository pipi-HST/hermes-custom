"""Capture repository — CRUD operations on captures table."""

from __future__ import annotations

import sqlite3
import uuid
import json
from datetime import datetime, timezone
from typing import Optional

from db.concurrency import fetch_dict, fetch_dicts, transaction


class CaptureRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, *, file_path: str = "", raw_path: str = "") -> dict:
        capture_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            "INSERT INTO captures (id, file_path, raw_path) VALUES (?, ?, ?)",
            (capture_id, file_path, raw_path),
        )
        self.conn.commit()
        return self.get_by_id(capture_id)

    def get_by_id(self, capture_id: str) -> Optional[dict]:
        return fetch_dict(self.conn, "SELECT * FROM captures WHERE id = ?", (capture_id,))

    def list_all(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        tag: str = "",
        search: str = "",
        folder: str = "",
    ) -> list[dict]:
        # tag/search/folder are applied after Markdown frontmatter hydration by
        # callers that know the vault path.  Accept them here so older API code
        # does not crash against the slim DB repository.
        rows = self.conn.execute(
            "SELECT * FROM captures ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_latest(self) -> Optional[dict]:
        return fetch_dict(
            self.conn,
            "SELECT * FROM captures ORDER BY created_at DESC LIMIT 1",
        )

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM captures").fetchone()
        return int(row["n"]) if row else 0

    def update(self, capture_id: str, **kwargs) -> Optional[dict]:
        allowed = {
            "file_path", "raw_path", "embedding",
            "review_state", "review_interval",
            "review_count", "last_reviewed", "next_review", "theme_scanned_at",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.get_by_id(capture_id)
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        sets = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [capture_id]
        self.conn.execute(
            f"UPDATE captures SET {sets} WHERE id = ?", values
        )
        self.conn.commit()
        return self.get_by_id(capture_id)

    def log_change(self, entity_type: str, entity_id: str, field: str,
                   old_value: str = "", new_value: str = "", source: str = "") -> None:
        self.conn.execute(
            """INSERT INTO change_log (entity_type, entity_id, field, old_value, new_value, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (entity_type, entity_id, field, old_value, new_value, source,
             datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        return dict(row)


class CaptureDraftRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        *,
        raw_text: str,
        source: str = "",
        source_type: str = "",
        title: str = "",
        word_count: int = 0,
        draft_text: str = "",
        instruction: str = "",
        capture_intent: dict | None = None,
        summary: str = "",
        tags: list[str] | None = None,
        status: str = "raw_extracted",
    ) -> dict:
        draft_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO capture_drafts
               (id, raw_text, source, source_type, title, word_count,
                draft_text, instruction, capture_intent, summary, tags, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                draft_id,
                raw_text,
                source,
                source_type,
                title,
                word_count,
                draft_text,
                instruction,
                json.dumps(capture_intent or {}, ensure_ascii=False),
                summary,
                json.dumps(tags or [], ensure_ascii=False),
                status,
            ),
        )
        self.conn.commit()
        return self.get_by_id(draft_id)

    def get_by_id(self, draft_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM capture_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def claim_for_commit(self, draft_id: str) -> Optional[dict]:
        updated_at = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            """UPDATE capture_drafts
               SET status = 'committing', updated_at = ?
               WHERE id = ?
                 AND (
                    status IN ('raw_extracted', 'draft_ready', 'revision_requested')
                    OR (
                        status = 'committing'
                        AND julianday(updated_at) <= julianday('now', '-15 minutes')
                    )
                 )""",
            (updated_at, draft_id),
        )
        self.conn.commit()
        if cursor.rowcount != 1:
            return None
        return self.get_by_id(draft_id)

    def commit_claim_is_stale(self, draft_id: str) -> bool:
        row = self.conn.execute(
            """SELECT 1 FROM capture_drafts
               WHERE id = ?
                 AND status = 'committing'
                 AND julianday(updated_at) <= julianday('now', '-15 minutes')""",
            (draft_id,),
        ).fetchone()
        return row is not None

    def restore_commit_status(self, draft_id: str, status: str) -> None:
        self.conn.execute(
            """UPDATE capture_drafts
               SET status = ?, updated_at = ?
               WHERE id = ? AND status = 'committing'""",
            (status, datetime.now(timezone.utc).isoformat(), draft_id),
        )
        self.conn.commit()

    def reserve_capture_for_commit(self, draft_id: str) -> str:
        with transaction(self.conn):
            row = self.conn.execute(
                """SELECT status, pending_capture_id
                   FROM capture_drafts WHERE id = ?""",
                (draft_id,),
            ).fetchone()
            if row is None or row["status"] != "committing":
                raise RuntimeError("draft commit claim was lost")

            capture_id = str(row["pending_capture_id"] or "")
            if not capture_id:
                capture_id = uuid.uuid4().hex[:12]
                self.conn.execute(
                    "INSERT INTO captures (id, file_path, raw_path) VALUES (?, '', '')",
                    (capture_id,),
                )
                cursor = self.conn.execute(
                    """UPDATE capture_drafts
                       SET pending_capture_id = ?, updated_at = ?
                       WHERE id = ? AND status = 'committing'""",
                    (
                        capture_id,
                        datetime.now(timezone.utc).isoformat(),
                        draft_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("draft commit claim was lost")
            else:
                self.conn.execute(
                    """INSERT OR IGNORE INTO captures (id, file_path, raw_path)
                       VALUES (?, '', '')""",
                    (capture_id,),
                )
            return capture_id

    def get_latest_open(self) -> Optional[dict]:
        row = self.conn.execute(
            """SELECT * FROM capture_drafts
               WHERE status IN ('raw_extracted', 'draft_ready', 'revision_requested', 'draft_incomplete')
               ORDER BY datetime(replace(substr(created_at, 1, 19), 'T', ' ')) DESC,
                        rowid DESC
               LIMIT 1"""
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def find_duplicate(self, *, source: str = "", raw_text: str = "") -> Optional[dict]:
        clauses = []
        values: list[str] = []
        if source:
            clauses.append("source = ?")
            values.append(source)
        if raw_text:
            clauses.append("raw_text = ?")
            values.append(raw_text)
        if not clauses:
            return None
        row = self.conn.execute(
            f"""SELECT * FROM capture_drafts
                WHERE ({' OR '.join(clauses)})
                ORDER BY
                    CASE
                        WHEN status IN ('raw_extracted', 'draft_ready', 'revision_requested', 'draft_incomplete') THEN 0
                        WHEN status = 'committed' THEN 1
                        ELSE 2
                    END,
                    datetime(replace(substr(updated_at, 1, 19), 'T', ' ')) DESC,
                    rowid DESC
                LIMIT 1""",
            values,
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def update(self, draft_id: str, **kwargs) -> Optional[dict]:
        allowed = {
            "draft_text", "instruction", "capture_intent", "summary", "tags", "status",
            "committed_capture_id", "title", "source", "source_type",
            "word_count", "raw_text", "pending_capture_id",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.get_by_id(draft_id)
        if "tags" in updates:
            updates["tags"] = json.dumps(updates["tags"] or [], ensure_ascii=False)
        if "capture_intent" in updates:
            updates["capture_intent"] = json.dumps(updates["capture_intent"] or {}, ensure_ascii=False)
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        sets = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [draft_id]
        self.conn.execute(
            f"UPDATE capture_drafts SET {sets} WHERE id = ?", values
        )
        self.conn.commit()
        return self.get_by_id(draft_id)

    def list_open(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """SELECT * FROM capture_drafts
               WHERE status IN ('raw_extracted', 'draft_ready', 'revision_requested', 'draft_incomplete')
               ORDER BY datetime(replace(substr(created_at, 1, 19), 'T', ' ')) DESC,
                        rowid DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        result = dict(row)
        try:
            result["tags"] = json.loads(result.get("tags") or "[]")
        except json.JSONDecodeError:
            result["tags"] = []
        try:
            result["capture_intent"] = json.loads(result.get("capture_intent") or "{}")
        except json.JSONDecodeError:
            result["capture_intent"] = {}
        return result
