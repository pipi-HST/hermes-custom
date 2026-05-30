"""Read-only Life Dashboard API routes."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query


def create_life_router(conn: sqlite3.Connection) -> APIRouter:
    router = APIRouter()

    @router.get("/life/summary")
    def get_life_summary(date: str = Query(default="")):
        selected_date = date or datetime.now().astimezone().date().isoformat()
        events = _events_on_date(conn, selected_date, limit=10000)
        reminders = _reminders_on_date(conn, selected_date, limit=10000)
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
        items = _events_on_date(conn, selected_date, limit=limit)
        return {"date": selected_date, "items": items, "total": len(items)}

    @router.get("/life/reminders")
    def list_life_reminders(
        date: str = Query(default=""),
        limit: int = Query(default=100, le=500),
    ):
        selected_date = date or datetime.now().astimezone().date().isoformat()
        items = _reminders_on_date(conn, selected_date, limit=limit)
        return {"date": selected_date, "items": items, "total": len(items)}

    return router


def _events_on_date(conn: sqlite3.Connection, selected_date: str, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM life_events
           ORDER BY occurred_at DESC, rowid DESC"""
    ).fetchall()
    items = [
        _event_row_to_dict(row)
        for row in rows
        if _date_key(row["occurred_at"]) == selected_date
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


def _event_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["payload_json"] = _load_json(item.get("payload_json"), {})
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
