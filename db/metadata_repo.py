"""Repository helpers for Agent-only capture metadata."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


class CaptureMetadataRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, capture_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM capture_metadata WHERE capture_id = ?",
            (capture_id,),
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        try:
            data["outline"] = json.loads(data.get("outline_json") or "[]")
        except json.JSONDecodeError:
            data["outline"] = []
        try:
            data["metadata"] = json.loads(data.get("metadata_json") or "{}")
        except json.JSONDecodeError:
            data["metadata"] = {}
        return data

    def set_folder_path(self, capture_id: str, folder_path: str) -> None:
        """Mirror the note's frontmatter folder into the indexed column.

        Frontmatter stays the source of truth; this column exists so retrieval
        and card inheritance can filter by topic without reading every note.
        """
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO capture_metadata (capture_id, folder_path, created_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(capture_id) DO UPDATE SET
                 folder_path=excluded.folder_path,
                 updated_at=excluded.updated_at""",
            (capture_id, str(folder_path or "Inbox"), now, now),
        )

    def upsert_outline(
        self,
        capture_id: str,
        outline: list[dict],
        *,
        source_hash: str,
        directory_rendered_at: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO capture_metadata
               (capture_id, outline_json, outline_source_hash,
                directory_rendered_at, metadata_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, '{}', ?, ?)
               ON CONFLICT(capture_id) DO UPDATE SET
                 outline_json=excluded.outline_json,
                 outline_source_hash=excluded.outline_source_hash,
                 directory_rendered_at=COALESCE(
                    excluded.directory_rendered_at,
                    capture_metadata.directory_rendered_at
                 ),
                 updated_at=excluded.updated_at""",
            (
                capture_id,
                json.dumps(outline, ensure_ascii=False),
                source_hash,
                directory_rendered_at,
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get(capture_id) or {}
