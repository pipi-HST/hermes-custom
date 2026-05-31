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

    @router.get("/life/week")
    def get_life_week(date: str = Query(default="")):
        selected_date = date or datetime.now().astimezone().date().isoformat()
        week_start, week_end = _week_bounds(selected_date)
        days = _date_range(week_start, week_end)
        with _life_connection(conn, db_path) as db:
            events = _events_between(db, week_start, week_end)
            weekly_review = _weekly_review_for_date(db, selected_date)
        trend_rows = [_trend_row_for_day(day, events) for day in days]
        metrics = _weekly_metrics(trend_rows)
        return {
            "date": selected_date,
            "week_start": week_start,
            "week_end": week_end,
            "metrics": metrics,
            "trend_rows": trend_rows,
            "record_groups": _weekly_record_groups(events),
            "weekly_review": weekly_review,
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


def _events_between(conn: sqlite3.Connection, week_start: str, week_end: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM life_events
           ORDER BY occurred_at DESC, rowid DESC"""
    ).fetchall()
    return [
        _event_row_to_dict(row)
        for row in rows
        if week_start <= _event_life_day(row) <= week_end
        and row["status"] not in {"undone", "superseded"}
    ]


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
    item["evidence_json"] = _load_json(item.get("evidence_json"), [])
    item["delivery_json"] = _load_json(item.get("delivery_json"), {})
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


def _event_life_day(row: sqlite3.Row) -> str:
    payload = _load_json(row["payload_json"], {})
    if row["event_type"] == "sleep_end" and payload.get("wake_date"):
        return str(payload["wake_date"])
    return _date_key(row["occurred_at"])


def _week_bounds(selected_date: str) -> tuple[str, str]:
    try:
        parsed = date_type.fromisoformat(selected_date)
    except ValueError:
        parsed = datetime.now().astimezone().date()
    start = parsed - timedelta(days=parsed.weekday())
    end = start + timedelta(days=6)
    return start.isoformat(), end.isoformat()


def _date_range(start: str, end: str) -> list[str]:
    current = date_type.fromisoformat(start)
    finish = date_type.fromisoformat(end)
    days: list[str] = []
    while current <= finish:
        days.append(current.isoformat())
        current = current + timedelta(days=1)
    return days


def _trend_row_for_day(day: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    day_events = [event for event in events if _event_dict_life_day(event) == day]
    sleep_minutes = sum(
        int(event.get("duration_minutes") or 0)
        for event in day_events
        if event["event_type"] == "sleep_end"
    )
    exercise_minutes = sum(
        int(event.get("duration_minutes") or 0)
        for event in day_events
        if event["event_type"] == "exercise"
    )
    sitting_total = sum(
        int(event.get("duration_minutes") or 0)
        for event in day_events
        if event["event_type"] == "sedentary_start"
    )
    sedentary_over_limit = sum(
        int(event.get("duration_minutes") or 0)
        for event in day_events
        if event["event_type"] == "sedentary_start"
        and int(event.get("duration_minutes") or 0) >= 60
    )
    return {
        "date": day,
        "sleep_minutes": sleep_minutes,
        "exercise_count": sum(1 for event in day_events if event["event_type"] == "exercise"),
        "exercise_minutes": exercise_minutes,
        "sitting_total_minutes": sitting_total,
        "sedentary_over_limit_minutes": sedentary_over_limit,
        "meal_count": sum(1 for event in day_events if event["event_type"] == "meal"),
        "water_count": sum(1 for event in day_events if event["event_type"] == "water"),
    }


def _weekly_metrics(trend_rows: list[dict[str, Any]]) -> dict[str, Any]:
    sleep_days = sum(1 for row in trend_rows if row["sleep_minutes"])
    total_sleep = sum(int(row["sleep_minutes"]) for row in trend_rows)
    exercise_days = sum(1 for row in trend_rows if row["exercise_count"])
    return {
        "sleep_days": sleep_days,
        "sleep_total_minutes": total_sleep,
        "sleep_average_minutes": round(total_sleep / sleep_days) if sleep_days else 0,
        "exercise_days": exercise_days,
        "exercise_total_minutes": sum(int(row["exercise_minutes"]) for row in trend_rows),
        "sitting_total_minutes": sum(int(row["sitting_total_minutes"]) for row in trend_rows),
        "sedentary_over_limit_minutes": sum(int(row["sedentary_over_limit_minutes"]) for row in trend_rows),
        "meal_count": sum(int(row["meal_count"]) for row in trend_rows),
        "water_count": sum(int(row["water_count"]) for row in trend_rows),
    }


def _weekly_record_groups(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "sleep": [event for event in events if event["event_type"] in {"sleep_start", "sleep_end"}],
        "meal": [event for event in events if event["event_type"] == "meal"],
        "exercise": [event for event in events if event["event_type"] == "exercise"],
        "sedentary": [event for event in events if event["event_type"] in {"sedentary_start", "sedentary_break"}],
        "water": [event for event in events if event["event_type"] == "water"],
    }


def _event_dict_life_day(event: dict[str, Any]) -> str:
    payload = event.get("payload_json") or {}
    if event["event_type"] == "sleep_end" and payload.get("wake_date"):
        return str(payload["wake_date"])
    return _date_key(str(event["occurred_at"]))
