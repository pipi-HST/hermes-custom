"""Repository for Reading Session state."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


class ReadingRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_session(
        self,
        *,
        source_type: str,
        source_id: str = "",
        source_url: str = "",
        source_title: str = "",
        focus: str = "",
        meta: dict[str, Any] | None = None,
    ) -> dict:
        session_id = uuid.uuid4().hex[:12]
        now = _now()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        self.conn.execute(
            """INSERT INTO reading_sessions
               (id, source_type, source_id, source_url, source_title, focus,
                expires_at, meta_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                source_type,
                source_id,
                source_url,
                source_title,
                focus,
                expires_at,
                _json(meta or {}),
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM reading_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def get_latest_active(self) -> dict | None:
        row = self.conn.execute(
            """SELECT * FROM reading_sessions
               WHERE status = 'active'
               ORDER BY datetime(replace(substr(updated_at, 1, 19), 'T', ' ')) DESC,
                        rowid DESC
               LIMIT 1"""
        ).fetchone()
        return _row_to_dict(row) if row else None

    def update_session(self, session_id: str, **kwargs) -> dict | None:
        allowed = {
            "source_type",
            "source_id",
            "source_url",
            "source_title",
            "status",
            "focus",
            "expires_at",
            "committed_artifact_id",
            "last_turn_at",
            "meta_json",
        }
        updates = {key: value for key, value in kwargs.items() if key in allowed}
        if "meta_json" in updates and isinstance(updates["meta_json"], dict):
            updates["meta_json"] = _json(updates["meta_json"])
        if not updates:
            return self.get_session(session_id)
        updates["updated_at"] = _now()
        sets = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values()) + [session_id]
        self.conn.execute(
            f"UPDATE reading_sessions SET {sets} WHERE id = ?",
            values,
        )
        self.conn.commit()
        return self.get_session(session_id)

    def add_turn(
        self,
        session_id: str,
        *,
        user_message: str,
        assistant_answer: str,
        focus: str = "",
        confidence: str = "medium",
        meta: dict[str, Any] | None = None,
    ) -> dict:
        turn_id = uuid.uuid4().hex[:12]
        row = self.conn.execute(
            "SELECT COALESCE(MAX(turn_index), 0) AS max_turn FROM reading_turns WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        turn_index = int(row["max_turn"] or 0) + 1
        now = _now()
        self.conn.execute(
            """INSERT INTO reading_turns
               (id, session_id, turn_index, user_message, assistant_answer,
                focus, confidence, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                turn_id,
                session_id,
                turn_index,
                user_message,
                assistant_answer,
                focus,
                confidence,
                _json(meta or {}),
                now,
            ),
        )
        self.conn.execute(
            "UPDATE reading_sessions SET last_turn_at = ?, updated_at = ? WHERE id = ?",
            (now, now, session_id),
        )
        self.conn.commit()
        return self.get_turn(turn_id)

    def get_turn(self, turn_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM reading_turns WHERE id = ?",
            (turn_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def list_turns(self, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM reading_turns WHERE session_id = ? ORDER BY turn_index ASC",
            (session_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def add_evidence(
        self,
        *,
        session_id: str,
        turn_id: str = "",
        source_kind: str,
        source_id: str = "",
        title: str = "",
        url: str = "",
        excerpt: str = "",
        used_for: str = "",
        meta: dict[str, Any] | None = None,
    ) -> dict:
        evidence_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO reading_evidence
               (id, session_id, turn_id, source_kind, source_id, title, url,
                excerpt, used_for, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evidence_id,
                session_id,
                turn_id,
                source_kind,
                source_id,
                title,
                url,
                excerpt,
                used_for,
                _json(meta or {}),
                _now(),
            ),
        )
        self.conn.commit()
        return self.get_evidence(evidence_id)

    def get_evidence(self, evidence_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM reading_evidence WHERE id = ?",
            (evidence_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def list_evidence(self, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM reading_evidence WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def create_draft(
        self,
        *,
        session_id: str,
        output_type: str,
        target_capture_id: str = "",
        title: str = "",
        summary: str = "",
        draft_text: str = "",
        meta: dict[str, Any] | None = None,
    ) -> dict:
        draft_id = uuid.uuid4().hex[:12]
        now = _now()
        self.conn.execute(
            """INSERT INTO reading_drafts
               (id, session_id, output_type, target_capture_id, title, summary,
                draft_text, meta_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                draft_id,
                session_id,
                output_type,
                target_capture_id,
                title,
                summary,
                draft_text,
                _json(meta or {}),
                now,
                now,
            ),
        )
        self.update_session(session_id, status="summarized")
        return self.get_draft(draft_id)

    def get_draft(self, draft_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM reading_drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def get_latest_open_draft(self) -> dict | None:
        row = self.conn.execute(
            """SELECT * FROM reading_drafts
               WHERE status = 'open'
               ORDER BY datetime(replace(substr(created_at, 1, 19), 'T', ' ')) DESC,
                        rowid DESC
               LIMIT 1"""
        ).fetchone()
        return _row_to_dict(row) if row else None

    def update_draft(self, draft_id: str, **kwargs) -> dict | None:
        allowed = {
            "output_type",
            "target_capture_id",
            "title",
            "summary",
            "draft_text",
            "status",
            "meta_json",
        }
        updates = {key: value for key, value in kwargs.items() if key in allowed}
        if "meta_json" in updates and isinstance(updates["meta_json"], dict):
            updates["meta_json"] = _json(updates["meta_json"])
        if not updates:
            return self.get_draft(draft_id)
        updates["updated_at"] = _now()
        sets = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values()) + [draft_id]
        self.conn.execute(
            f"UPDATE reading_drafts SET {sets} WHERE id = ?",
            values,
        )
        self.conn.commit()
        return self.get_draft(draft_id)

    def record_capture_backup(
        self,
        *,
        capture_id: str,
        reading_draft_id: str,
        backup_file_path: str,
        previous_file_hash: str = "",
        new_file_hash: str = "",
        meta: dict[str, Any] | None = None,
    ) -> dict:
        backup_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO capture_version_backups
               (id, capture_id, reading_draft_id, backup_file_path,
                previous_file_hash, new_file_hash, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                backup_id,
                capture_id,
                reading_draft_id,
                backup_file_path,
                previous_file_hash,
                new_file_hash,
                _json(meta or {}),
                _now(),
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM capture_version_backups WHERE id = ?",
            (backup_id,),
        ).fetchone()
        return _row_to_dict(row)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _row_to_dict(row: sqlite3.Row) -> dict:
    result = dict(row)
    for key in ("meta_json",):
        if key in result:
            try:
                result[key] = json.loads(result.get(key) or "{}")
            except (json.JSONDecodeError, TypeError):
                result[key] = {}
    return result
