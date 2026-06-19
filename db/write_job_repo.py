"""Repository for Knowledge write compensation jobs."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


class KnowledgeWriteJobRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def start(
        self,
        *,
        intent: str,
        object_type: str,
        object_id: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex
        now = _now()
        self.conn.execute(
            """INSERT INTO knowledge_write_jobs
               (id, intent, object_type, object_id, status, step, payload_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending', '', ?, ?, ?)""",
            (
                job_id,
                intent,
                object_type,
                object_id,
                _json(payload or {}),
                now,
                now,
            ),
        )
        self.conn.commit()
        return job_id

    def mark_step(
        self,
        job_id: str,
        *,
        status: str,
        step: str,
        file_path: str = "",
        db_table: str = "",
    ) -> None:
        self.conn.execute(
            """UPDATE knowledge_write_jobs
               SET status = ?, step = ?, file_path = COALESCE(NULLIF(?, ''), file_path),
                   db_table = COALESCE(NULLIF(?, ''), db_table), updated_at = ?
               WHERE id = ?""",
            (status, step, file_path, db_table, _now(), job_id),
        )
        self.conn.commit()

    def mark_retrying(self, job_id: str, *, step: str, error: str = "") -> None:
        self.conn.execute(
            """UPDATE knowledge_write_jobs
               SET status = 'retrying', step = ?, error = ?,
                   retry_count = retry_count + 1, updated_at = ?
               WHERE id = ?""",
            (step, error, _now(), job_id),
        )
        self.conn.commit()

    def repair_needed(
        self,
        job_id: str,
        *,
        step: str,
        error: str,
        file_path: str = "",
        db_table: str = "",
    ) -> None:
        self.conn.execute(
            """UPDATE knowledge_write_jobs
               SET status = 'repair_needed', step = ?, error = ?,
                   file_path = COALESCE(NULLIF(?, ''), file_path),
                   db_table = COALESCE(NULLIF(?, ''), db_table),
                   updated_at = ?
               WHERE id = ?""",
            (step, error, file_path, db_table, _now(), job_id),
        )
        self.conn.commit()

    def complete(
        self,
        job_id: str,
        *,
        file_path: str = "",
        db_table: str = "",
    ) -> None:
        self.mark_step(
            job_id,
            status="completed",
            step="completed",
            file_path=file_path,
            db_table=db_table,
        )

    def fail(self, job_id: str, *, step: str, error: str, status: str = "failed") -> None:
        self.conn.execute(
            """UPDATE knowledge_write_jobs
               SET status = ?, step = ?, error = ?, updated_at = ?
               WHERE id = ?""",
            (status, step, error, _now(), job_id),
        )
        self.conn.commit()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
