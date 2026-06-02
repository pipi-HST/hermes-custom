#!/usr/bin/env python3
"""Dry-run-first Life Assistant data repair helper."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.life_audit import _load_db_path, _meal_quality_issues, _parse_datetime

DEFAULT_CRON_JOBS_PATH = "/Users/pipi/.hermes/cron/jobs.json"


def plan_repairs(
    *,
    db_path: str | None = None,
    completed_at: str = "",
    delivery_error: str = "",
    cron_jobs_path: str = DEFAULT_CRON_JOBS_PATH,
) -> dict[str, Any]:
    resolved_db_path = db_path or _load_db_path(ROOT / "config.yaml")
    completed = completed_at or _last_cron_run_at(cron_jobs_path)
    actions: list[dict[str, Any]] = []

    conn = sqlite3.connect(resolved_db_path)
    conn.row_factory = sqlite3.Row
    try:
        if completed:
            actions.extend(_stale_sending_actions(conn, completed_at=completed, delivery_error=delivery_error))
        actions.extend(_superseded_duplicate_reminder_actions(conn))
        actions.extend(_duplicate_sleep_actions(conn))
        actions.extend(_dirty_meal_actions(conn))
        actions.extend(_cross_midnight_sedentary_actions(conn))
    finally:
        conn.close()

    return {
        "status": "ok",
        "db_path": resolved_db_path,
        "completed_at": completed,
        "delivery_error": delivery_error,
        "summary": {
            "action_count": len(actions),
            "safe_apply_count": sum(1 for action in actions if action.get("safe_to_apply")),
        },
        "actions": actions,
    }


def apply_repairs(
    *,
    db_path: str | None = None,
    completed_at: str = "",
    delivery_error: str = "",
    cron_jobs_path: str = DEFAULT_CRON_JOBS_PATH,
) -> dict[str, Any]:
    planned = plan_repairs(
        db_path=db_path,
        completed_at=completed_at,
        delivery_error=delivery_error,
        cron_jobs_path=cron_jobs_path,
    )
    applied: list[dict[str, Any]] = []
    conn = sqlite3.connect(planned["db_path"])
    conn.row_factory = sqlite3.Row
    try:
        for action in planned["actions"]:
            if not action.get("safe_to_apply"):
                continue
            if action["kind"] == "stale_sending_reminder":
                _apply_stale_sending(conn, action)
                applied.append(action)
            elif action["kind"] == "superseded_duplicate_reminder":
                _apply_superseded_duplicate_reminder(conn, action)
                applied.append(action)
        conn.commit()
    finally:
        conn.close()
    return {
        **planned,
        "summary": {
            **planned["summary"],
            "applied_count": len(applied),
        },
        "applied": applied,
    }


def _stale_sending_actions(
    conn: sqlite3.Connection,
    *,
    completed_at: str,
    delivery_error: str,
) -> list[dict[str, Any]]:
    completed = _parse_datetime(completed_at)
    rows = conn.execute(
        """SELECT id, reminder_type, scheduled_for, dedupe_key, error
           FROM life_reminder_logs
           WHERE status = 'sending'
           ORDER BY scheduled_for ASC, rowid ASC"""
    ).fetchall()
    actions: list[dict[str, Any]] = []
    for row in rows:
        scheduled = _parse_datetime(str(row["scheduled_for"]))
        if scheduled > completed:
            continue
        target_status = "failed" if delivery_error else "sent"
        actions.append(
            {
                "kind": "stale_sending_reminder",
                "safe_to_apply": True,
                "id": str(row["id"]),
                "reminder_type": str(row["reminder_type"]),
                "dedupe_key": str(row["dedupe_key"]),
                "scheduled_for": str(row["scheduled_for"]),
                "target_status": target_status,
                "target_sent_at": completed_at if target_status == "sent" else "",
                "target_error": delivery_error,
            }
        )
    return actions


def _duplicate_sleep_actions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT COALESCE(json_extract(payload_json, '$.wake_date'), date(occurred_at)) AS wake_date,
                  occurred_at,
                  COALESCE(duration_minutes, -1) AS duration_minutes,
                  count(*) AS cnt,
                  group_concat(id, ',') AS ids
           FROM life_events
           WHERE event_type = 'sleep_end'
             AND status NOT IN ('undone', 'superseded')
           GROUP BY wake_date, occurred_at, duration_minutes
           HAVING cnt > 1
           ORDER BY wake_date DESC, occurred_at DESC"""
    ).fetchall()
    return [
        {
            "kind": "duplicate_sleep_session",
            "safe_to_apply": False,
            "wake_date": str(row["wake_date"]),
            "occurred_at": str(row["occurred_at"]),
            "duration_minutes": int(row["duration_minutes"]),
            "ids": str(row["ids"]).split(","),
            "suggestion": "manual review before superseding duplicates",
        }
        for row in rows
    ]


def _superseded_duplicate_reminder_actions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT pending.id,
                  pending.reminder_type,
                  pending.scheduled_for,
                  pending.dedupe_key,
                  newer.id AS superseded_by_id
           FROM life_reminder_logs AS pending
           JOIN life_reminder_logs AS newer
             ON newer.reminder_type = pending.reminder_type
            AND newer.scheduled_for = pending.scheduled_for
            AND newer.id != pending.id
            AND newer.status IN ('sent', 'completed')
           WHERE pending.status IN ('scheduled', 'sending')
           ORDER BY pending.scheduled_for ASC, pending.rowid ASC"""
    ).fetchall()
    seen: set[str] = set()
    actions: list[dict[str, Any]] = []
    for row in rows:
        reminder_id = str(row["id"])
        if reminder_id in seen:
            continue
        seen.add(reminder_id)
        actions.append(
            {
                "kind": "superseded_duplicate_reminder",
                "safe_to_apply": True,
                "id": reminder_id,
                "reminder_type": str(row["reminder_type"]),
                "dedupe_key": str(row["dedupe_key"]),
                "scheduled_for": str(row["scheduled_for"]),
                "target_status": "superseded",
                "superseded_by_id": str(row["superseded_by_id"]),
            }
        )
    return actions


def _dirty_meal_actions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {
            "kind": "dirty_meal_record",
            "safe_to_apply": False,
            **issue,
            "suggestion": "manual review; parser fixes should prevent new records",
        }
        for issue in _meal_quality_issues(conn)
    ]


def _cross_midnight_sedentary_actions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, occurred_at, ended_at, duration_minutes, status
           FROM life_events
           WHERE event_type = 'sedentary_start'
             AND ended_at IS NOT NULL
             AND ended_at != ''
             AND status NOT IN ('undone', 'superseded', 'day_split')
           ORDER BY occurred_at DESC, rowid DESC"""
    ).fetchall()
    actions: list[dict[str, Any]] = []
    for row in rows:
        started = _parse_datetime(str(row["occurred_at"]))
        ended = _parse_datetime(str(row["ended_at"]))
        if started.date() == ended.date():
            continue
        actions.append(
            {
                "kind": "cross_midnight_sedentary_anomaly",
                "safe_to_apply": False,
                "id": str(row["id"]),
                "started_at": str(row["occurred_at"]),
                "ended_at": str(row["ended_at"]),
                "duration_minutes": int(row["duration_minutes"] or 0),
                "status": str(row["status"]),
                "suggestion": "manual review; split or supersede only after confirming sleep/away period",
            }
        )
    return actions


def _apply_stale_sending(conn: sqlite3.Connection, action: dict[str, Any]) -> None:
    conn.execute(
        """UPDATE life_reminder_logs
           SET status = ?, sent_at = ?, error = ?, updated_at = ?
           WHERE id = ? AND status = 'sending'""",
        (
            action["target_status"],
            action["target_sent_at"] or None,
            action["target_error"],
            datetime.now().astimezone().isoformat(),
            action["id"],
        ),
    )


def _apply_superseded_duplicate_reminder(conn: sqlite3.Connection, action: dict[str, Any]) -> None:
    payload = {
        "superseded_by_id": action["superseded_by_id"],
        "superseded_reason": "duplicate_reminder_already_sent_or_completed",
    }
    conn.execute(
        """UPDATE life_reminder_logs
           SET status = 'superseded', payload_json = ?, updated_at = ?
           WHERE id = ? AND status IN ('scheduled', 'sending')""",
        (
            json.dumps(payload, ensure_ascii=False),
            datetime.now().astimezone().isoformat(),
            action["id"],
        ),
    )


def _last_cron_run_at(cron_jobs_path: str) -> str:
    try:
        data = json.loads(Path(cron_jobs_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return ""
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if job.get("name") == "life-assistant-tick" or job.get("script") == "life_tick_cron.py":
            return str(job.get("last_run_at") or "")
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or apply safe Life Assistant data repairs.")
    parser.add_argument("--db-path", default=None, help="SQLite DB path. Defaults to config.yaml.")
    parser.add_argument("--completed-at", default="", help="Delivery completion timestamp. Defaults to cron last_run_at.")
    parser.add_argument("--delivery-error", default="", help="Delivery error for stale sending reminders.")
    parser.add_argument("--cron-jobs-path", default=DEFAULT_CRON_JOBS_PATH)
    parser.add_argument("--apply", action="store_true", help="Apply safe repairs. Default is dry-run only.")
    args = parser.parse_args()

    if args.apply:
        report = apply_repairs(
            db_path=args.db_path,
            completed_at=args.completed_at,
            delivery_error=args.delivery_error,
            cron_jobs_path=args.cron_jobs_path,
        )
    else:
        report = plan_repairs(
            db_path=args.db_path,
            completed_at=args.completed_at,
            delivery_error=args.delivery_error,
            cron_jobs_path=args.cron_jobs_path,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
