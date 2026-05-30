"""Repository helpers for Life Assistant tables."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any


class LifeRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_event(
        self,
        *,
        event_type: str,
        occurred_at: str,
        ended_at: str | None = None,
        duration_minutes: int | None = None,
        source: str = "",
        raw_text: str = "",
        payload: dict[str, Any] | None = None,
        status: str = "confirmed",
    ) -> dict[str, Any]:
        event_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO life_events
               (id, event_type, occurred_at, ended_at, duration_minutes,
                source, raw_text, payload_json, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                event_type,
                occurred_at,
                ended_at,
                duration_minutes,
                source,
                raw_text,
                json.dumps(payload or {}, ensure_ascii=False),
                status,
            ),
        )
        self.conn.commit()
        return self.get_event(event_id) or {}

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM life_events WHERE id = ?", (event_id,)
        ).fetchone()
        return self._event_row_to_dict(row) if row else None

    def latest_user_event(
        self,
        *,
        now: str,
        window_minutes: int,
    ) -> dict[str, Any] | None:
        now_dt = _parse_datetime(now)
        rows = self.conn.execute(
            """SELECT * FROM life_events
               WHERE source = 'wechat'
               ORDER BY datetime(replace(substr(created_at, 1, 19), 'T', ' ')) DESC,
                        rowid DESC"""
        ).fetchall()
        for row in rows:
            item = self._event_row_to_dict(row)
            if not item:
                continue
            occurred = _parse_datetime(str(item["occurred_at"]))
            if occurred.date() != now_dt.date():
                continue
            elapsed_minutes = abs(_seconds_between(occurred, now_dt)) / 60
            if elapsed_minutes <= window_minutes:
                return item
        return None

    def update_event(
        self,
        event_id: str,
        *,
        event_type: str | None = None,
        occurred_at: str | None = None,
        ended_at: str | None = None,
        duration_minutes: int | None = None,
        payload: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        updates: dict[str, Any] = {}
        if event_type is not None:
            updates["event_type"] = event_type
        if occurred_at is not None:
            updates["occurred_at"] = occurred_at
        if ended_at is not None:
            updates["ended_at"] = ended_at
        if duration_minutes is not None:
            updates["duration_minutes"] = duration_minutes
        if payload is not None:
            updates["payload_json"] = json.dumps(payload, ensure_ascii=False)
        if status is not None:
            updates["status"] = status
        if not updates:
            return self.get_event(event_id)
        updates["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{key} = ?" for key in updates)
        self.conn.execute(
            f"UPDATE life_events SET {sets} WHERE id = ?",
            [*updates.values(), event_id],
        )
        self.conn.commit()
        return self.get_event(event_id)

    def create_reminder_log(
        self,
        *,
        reminder_type: str,
        scheduled_for: str,
        status: str,
        dedupe_key: str,
        rule_id: str = "",
        sent_at: str | None = None,
        payload: dict[str, Any] | None = None,
        error: str = "",
    ) -> dict[str, Any]:
        existing = self.conn.execute(
            "SELECT * FROM life_reminder_logs WHERE dedupe_key = ?",
            (dedupe_key,),
        ).fetchone()
        if existing:
            return self._reminder_row_to_dict(existing)

        log_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO life_reminder_logs
               (id, rule_id, reminder_type, scheduled_for, sent_at, status,
                dedupe_key, error, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                log_id,
                rule_id,
                reminder_type,
                scheduled_for,
                sent_at,
                status,
                dedupe_key,
                error,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM life_reminder_logs WHERE id = ?", (log_id,)
        ).fetchone()
        return self._reminder_row_to_dict(row)

    def count_reminder_logs(self, reminder_type: str) -> int:
        row = self.conn.execute(
            "SELECT count(*) AS cnt FROM life_reminder_logs WHERE reminder_type = ?",
            (reminder_type,),
        ).fetchone()
        return int(row["cnt"] if row else 0)

    def _event_row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["payload_json"] = _load_json(result.get("payload_json"), {})
        return result

    def _reminder_row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any]:
        result = dict(row or {})
        result["payload_json"] = _load_json(result.get("payload_json"), {})
        return result


def _load_json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.strptime(value[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")


def _seconds_between(start: datetime, end: datetime) -> float:
    start, end = _compatible_datetime_pair(start, end)
    return (end - start).total_seconds()


def _compatible_datetime_pair(left: datetime, right: datetime) -> tuple[datetime, datetime]:
    if _is_aware(left) == _is_aware(right):
        return left, right
    return left.replace(tzinfo=None), right.replace(tzinfo=None)


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
