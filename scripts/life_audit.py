#!/usr/bin/env python3
"""Read-only runtime audit for Life Assistant."""

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

from db.life_schema import migrate_life_schema

LIFE_TABLES = {
    "life_events",
    "life_reminder_rules",
    "life_reminder_logs",
    "life_locks",
    "life_daily_reviews",
    "life_weekly_reviews",
    "life_long_term_decisions",
    "life_decision_reviews",
}


def run_audit(
    *,
    db_path: str | None = None,
    now: str | None = None,
    stale_active_hours: int = 12,
    migrate: bool = False,
) -> dict[str, Any]:
    resolved_db_path = db_path or _load_db_path(ROOT / "config.yaml")
    current = _parse_datetime(now) if now else datetime.now().astimezone()
    checks: list[dict[str, Any]] = []
    summary: dict[str, Any] = {"db_path": resolved_db_path, "now": current.isoformat()}

    conn = sqlite3.connect(resolved_db_path)
    conn.row_factory = sqlite3.Row
    try:
        if migrate:
            migrate_life_schema(conn)
        existing_tables = _table_names(conn)
        missing_tables = sorted(LIFE_TABLES - existing_tables)
        _check(
            checks,
            "life_tables",
            "failed" if missing_tables else "ok",
            "missing=" + ",".join(missing_tables) if missing_tables else "all Life tables exist",
        )
        if missing_tables:
            return _finish(summary, checks)

        event_counts = _count_by(conn, "life_events", "event_type", "status")
        reminder_counts = _count_by(conn, "life_reminder_logs", "reminder_type", "status")
        summary["event_counts"] = event_counts
        summary["reminder_counts"] = reminder_counts
        summary["daily_reviews"] = _scalar(conn, "SELECT count(*) FROM life_daily_reviews")
        summary["weekly_reviews"] = _scalar(conn, "SELECT count(*) FROM life_weekly_reviews")
        summary["long_term_decisions"] = _count_by(conn, "life_long_term_decisions", "status")

        active_windows = conn.execute(
            """SELECT id, occurred_at FROM life_events
               WHERE event_type = 'sedentary_start' AND status = 'active'
               ORDER BY occurred_at"""
        ).fetchall()
        summary["active_sitting_windows"] = len(active_windows)
        _check(
            checks,
            "active_sitting_window_count",
            "failed" if len(active_windows) > 1 else "ok",
            f"{len(active_windows)} active sitting window(s)",
        )
        if active_windows:
            oldest = _parse_datetime(active_windows[0]["occurred_at"])
            age_minutes = int((current - oldest).total_seconds() // 60)
            summary["active_sitting_oldest_age_minutes"] = age_minutes
            _check(
                checks,
                "active_sitting_window_age",
                "warning" if age_minutes > stale_active_hours * 60 else "ok",
                f"oldest active sitting window is {age_minutes} minutes old",
            )

        failed_reminders = _scalar(
            conn,
            "SELECT count(*) FROM life_reminder_logs WHERE status = 'failed'",
        )
        summary["failed_reminders"] = failed_reminders
        _check(
            checks,
            "failed_reminders",
            "warning" if failed_reminders else "ok",
            f"{failed_reminders} failed reminder log(s)",
        )

        pending_decisions = _scalar(
            conn,
            "SELECT count(*) FROM life_long_term_decisions WHERE status = 'pending_review_time'",
        )
        summary["pending_long_term_decisions"] = pending_decisions
        _check(
            checks,
            "pending_long_term_decisions",
            "warning" if pending_decisions else "ok",
            f"{pending_decisions} decision(s) waiting for review time",
        )

        sending_logs = _scalar(
            conn,
            "SELECT count(*) FROM life_reminder_logs WHERE status = 'sending'",
        )
        summary["sending_reminders"] = sending_logs
        _check(
            checks,
            "stuck_sending_reminders",
            "warning" if sending_logs else "ok",
            f"{sending_logs} reminder log(s) still sending",
        )

        duplicate_sleep_sessions = _duplicate_sleep_sessions(conn)
        summary["duplicate_sleep_sessions"] = len(duplicate_sleep_sessions)
        if duplicate_sleep_sessions:
            summary["duplicate_sleep_session_examples"] = duplicate_sleep_sessions[:5]
        _check(
            checks,
            "duplicate_sleep_sessions",
            "warning" if duplicate_sleep_sessions else "ok",
            f"{len(duplicate_sleep_sessions)} duplicate sleep session group(s)",
        )

        reminder_bursts = _reminder_bursts(conn)
        summary["reminder_bursts"] = reminder_bursts
        _check(
            checks,
            "reminder_bursts",
            "warning" if reminder_bursts else "ok",
            f"{len(reminder_bursts)} reminder burst(s)",
        )
    finally:
        conn.close()

    return _finish(summary, checks)


def _load_db_path(config_path: Path) -> str:
    try:
        import yaml

        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, Exception):
        return str(ROOT / "data" / "captures.db")
    return str(cfg.get("database", {}).get("path") or ROOT / "data" / "captures.db")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def _seconds_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }


def _count_by(conn: sqlite3.Connection, table: str, *columns: str) -> dict[str, Any]:
    select = ", ".join(columns)
    group = ", ".join(columns)
    rows = conn.execute(
        f"SELECT {select}, count(*) AS cnt FROM {table} GROUP BY {group} ORDER BY {group}"
    ).fetchall()
    result: dict[str, Any] = {}
    for row in rows:
        cursor = result
        for column in columns[:-1]:
            cursor = cursor.setdefault(str(row[column]), {})
        cursor[str(row[columns[-1]])] = row["cnt"]
    return result


def _scalar(conn: sqlite3.Connection, query: str) -> int:
    row = conn.execute(query).fetchone()
    return int(row[0] if row else 0)


def _duplicate_sleep_sessions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
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
            "wake_date": str(row["wake_date"]),
            "occurred_at": str(row["occurred_at"]),
            "duration_minutes": int(row["duration_minutes"]),
            "count": int(row["cnt"]),
            "ids": str(row["ids"]).split(","),
        }
        for row in rows
    ]


def _reminder_bursts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    min_gap_minutes = {
        "exercise": 120,
        "sedentary": 10,
    }
    rows = conn.execute(
        """SELECT reminder_type, scheduled_for, status
           FROM life_reminder_logs
           WHERE reminder_type IN ('exercise', 'sedentary')
             AND status IN ('scheduled', 'sending', 'sent')
           ORDER BY reminder_type ASC, scheduled_for ASC, rowid ASC"""
    ).fetchall()
    bursts: list[dict[str, Any]] = []
    previous_by_type: dict[str, str] = {}
    for row in rows:
        reminder_type = str(row["reminder_type"])
        scheduled_for = str(row["scheduled_for"])
        previous = previous_by_type.get(reminder_type)
        previous_by_type[reminder_type] = scheduled_for
        if not previous:
            continue
        current_dt = _parse_datetime(scheduled_for)
        previous_dt = _parse_datetime(previous)
        if current_dt.date() != previous_dt.date():
            continue
        gap_minutes = int(_seconds_between(previous_dt, current_dt) // 60)
        if gap_minutes < min_gap_minutes[reminder_type]:
            bursts.append(
                {
                    "reminder_type": reminder_type,
                    "previous_scheduled_for": previous,
                    "scheduled_for": scheduled_for,
                    "gap_minutes": gap_minutes,
                    "expected_min_gap_minutes": min_gap_minutes[reminder_type],
                }
            )
    return bursts[:20]


def _check(checks: list[dict[str, Any]], name: str, status: str, detail: str) -> None:
    checks.append({"name": name, "status": status, "detail": detail})


def _finish(summary: dict[str, Any], checks: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = {check["status"] for check in checks}
    if "failed" in statuses:
        status = "failed"
    elif "warning" in statuses:
        status = "warning"
    else:
        status = "ok"
    return {"status": status, "summary": summary, "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only Life Assistant runtime audit.")
    parser.add_argument("--db-path", default=None, help="SQLite DB path. Defaults to config.yaml.")
    parser.add_argument("--now", default=None, help="Optional ISO timestamp for deterministic checks.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero for warnings as well as failures.")
    parser.add_argument("--migrate", action="store_true", help="Create missing Life tables before auditing.")
    args = parser.parse_args()

    report = run_audit(db_path=args.db_path, now=args.now, migrate=args.migrate)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] == "failed" or (args.strict and report["status"] == "warning"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
