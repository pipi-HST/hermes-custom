"""Read-only Life Dashboard API routes."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date as date_type, datetime, timedelta
from typing import Any, Iterator

from fastapi import APIRouter, Query


def create_life_router(
    conn: sqlite3.Connection | None = None,
    *,
    db_path: str | None = None,
) -> APIRouter:
    if conn is None and db_path is None:
        raise ValueError("create_life_router requires conn or db_path")

    router = APIRouter()

    @router.get("/life/summary")
    def get_life_summary(date: str = Query(default="")):
        selected_date = date or datetime.now().astimezone().date().isoformat()
        with _life_connection(conn, db_path) as db:
            events = _events_on_date(db, selected_date, limit=10000)
            reminders = _reminders_on_date(db, selected_date, limit=10000)
        sitting_windows = [
            event for event in events
            if event["event_type"] == "sedentary_start" and event.get("status") != "active"
        ]
        active_sitting = next(
            (
                event for event in sorted(
                    events,
                    key=lambda item: item.get("occurred_at", ""),
                    reverse=True,
                )
                if event["event_type"] == "sedentary_start" and event.get("status") == "active"
            ),
            None,
        )
        event_counts: dict[str, int] = {}
        for event in events:
            event_counts[event["event_type"]] = event_counts.get(event["event_type"], 0) + 1
        latest_reminders: dict[str, dict[str, Any]] = {}
        for reminder in reminders:
            reminder_type = reminder["reminder_type"]
            current = latest_reminders.get(reminder_type)
            if not current or reminder["scheduled_for"] > current["scheduled_for"]:
                latest_reminders[reminder_type] = reminder
        return {
            "date": selected_date,
            "metrics": {
                "sitting_total_minutes": sum(int(event.get("duration_minutes") or 0) for event in sitting_windows),
                "sedentary_over_limit_minutes": sum(
                    int(event.get("duration_minutes") or 0)
                    for event in sitting_windows
                    if int(event.get("duration_minutes") or 0) >= 60
                ),
                "event_counts": event_counts,
            },
            "active_sitting": active_sitting,
            "latest_reminders": latest_reminders,
        }

    @router.get("/life/events")
    def list_life_events(
        date: str = Query(default=""),
        limit: int = Query(default=100, le=500),
    ):
        selected_date = date or datetime.now().astimezone().date().isoformat()
        with _life_connection(conn, db_path) as db:
            items = _events_on_date(db, selected_date, limit=limit)
        return {"date": selected_date, "items": items, "total": len(items)}

    @router.get("/life/reminders")
    def list_life_reminders(
        date: str = Query(default=""),
        limit: int = Query(default=100, le=500),
    ):
        selected_date = date or datetime.now().astimezone().date().isoformat()
        with _life_connection(conn, db_path) as db:
            items = _reminders_on_date(db, selected_date, limit=limit)
        return {"date": selected_date, "items": items, "total": len(items)}

    @router.get("/life/reviews")
    def get_life_reviews(date: str = Query(default="")):
        selected_date = date or datetime.now().astimezone().date().isoformat()
        with _life_connection(conn, db_path) as db:
            daily_review = _daily_review_on_date(db, selected_date)
            weekly_review = _weekly_review_for_date(db, selected_date)
            long_term_decisions = _long_term_decisions(db)
        return {
            "date": selected_date,
            "daily_review": daily_review,
            "weekly_review": weekly_review,
            "long_term_decisions": long_term_decisions,
        }

    return router


@contextmanager
def _life_connection(
    conn: sqlite3.Connection | None,
    db_path: str | None,
) -> Iterator[sqlite3.Connection]:
    if db_path is None:
        assert conn is not None
        yield conn
        return

    fresh = sqlite3.connect(db_path)
    fresh.row_factory = sqlite3.Row
    try:
        yield fresh
    finally:
        fresh.close()


def _events_on_date(conn: sqlite3.Connection, selected_date: str, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM life_events
           ORDER BY occurred_at DESC, rowid DESC"""
    ).fetchall()
    items = [
        _event_row_to_dict(row)
        for row in rows
        if _date_key(row["occurred_at"]) == selected_date
        and row["status"] not in {"undone", "superseded"}
    ]
    return items[:limit]


def _reminders_on_date(conn: sqlite3.Connection, selected_date: str, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM life_reminder_logs
           ORDER BY scheduled_for DESC, rowid DESC"""
    ).fetchall()
    items = [
        _reminder_row_to_dict(row)
        for row in rows
        if _date_key(row["scheduled_for"]) == selected_date
    ]
    return items[:limit]


def _daily_review_on_date(conn: sqlite3.Connection, selected_date: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM life_daily_reviews WHERE review_date = ?",
        (selected_date,),
    ).fetchone()
    if not row:
        return None
    return _daily_review_row_to_dict(row)


def _weekly_review_for_date(conn: sqlite3.Connection, selected_date: str) -> dict[str, Any] | None:
    week_start, week_end = _week_bounds(selected_date)
    row = conn.execute(
        """SELECT * FROM life_weekly_reviews
           WHERE week_start = ? AND week_end = ?""",
        (week_start, week_end),
    ).fetchone()
    if not row:
        return None
    return _weekly_review_row_to_dict(row)


def _long_term_decisions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM life_long_term_decisions
           WHERE status IN ('active', 'pending_review_time')
           ORDER BY next_review_at ASC, created_at DESC"""
    ).fetchall()
    return [_decision_row_to_dict(conn, row) for row in rows]


def _event_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["payload_json"] = _load_json(item.get("payload_json"), {})
    return item


def _daily_review_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["metrics_json"] = _load_json(item.get("metrics_json"), {})
    item["notes_json"] = _load_json(item.get("notes_json"), {})
    return item


def _weekly_review_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["labels_json"] = _load_json(item.get("labels_json"), {})
    item["patterns_json"] = _load_json(item.get("patterns_json"), {})
    item["recommendations_json"] = _load_json(item.get("recommendations_json"), [])
    return item


def _decision_row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    review = conn.execute(
        """SELECT * FROM life_decision_reviews
           WHERE decision_id = ?
           ORDER BY reviewed_at DESC, rowid DESC
           LIMIT 1""",
        (item["id"],),
    ).fetchone()
    item["latest_review"] = dict(review) if review else None
    return item


def _reminder_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["payload_json"] = _load_json(item.get("payload_json"), {})
    return item


def _load_json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _date_key(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)[:10]
    return parsed.date().isoformat()


def _week_bounds(selected_date: str) -> tuple[str, str]:
    try:
        parsed = date_type.fromisoformat(selected_date)
    except ValueError:
        parsed = datetime.now().astimezone().date()
    start = parsed - timedelta(days=parsed.weekday())
    end = start + timedelta(days=6)
    return start.isoformat(), end.isoformat()
