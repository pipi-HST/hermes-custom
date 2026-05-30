"""Tests for read-only Life Dashboard API routes."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.life import create_life_router
from db.life_repo import LifeRepo
from db.life_schema import init_life_db


def _client(tmp_path) -> tuple[TestClient, LifeRepo]:
    conn = init_life_db(str(tmp_path / "life.db"))
    app = FastAPI()
    app.include_router(create_life_router(conn), prefix="/api")
    return TestClient(app), LifeRepo(conn)


def test_life_summary_returns_daily_metrics(tmp_path) -> None:
    client, repo = _client(tmp_path)
    repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-30T09:00:00+08:00",
        ended_at="2026-05-30T09:50:00+08:00",
        duration_minutes=50,
        source="mac_activity",
        status="auto_resolved",
    )
    repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-30T10:00:00+08:00",
        ended_at="2026-05-30T11:20:00+08:00",
        duration_minutes=80,
        source="mac_activity",
        status="auto_resolved",
    )
    repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-30T12:00:00+08:00",
        source="mac_activity",
        status="active",
    )
    repo.create_event(
        event_type="water",
        occurred_at="2026-05-30T13:00:00+08:00",
        source="wechat",
        raw_text="喝水了",
    )
    repo.create_event(
        event_type="meal",
        occurred_at="2026-05-30T13:30:00+08:00",
        source="wechat",
        raw_text="午饭吃了牛肉饭",
        payload={"meal_slot": "lunch", "content": "牛肉饭"},
    )
    repo.create_reminder_log(
        reminder_type="exercise",
        scheduled_for="2026-05-30T20:00:00+08:00",
        status="scheduled",
        dedupe_key="exercise_reschedule:2026-05-30T20:00:00+08:00",
        payload={"source": "user_reschedule"},
    )

    response = client.get("/api/life/summary?date=2026-05-30")

    assert response.status_code == 200
    data = response.json()
    assert data["date"] == "2026-05-30"
    assert data["metrics"]["sitting_total_minutes"] == 130
    assert data["metrics"]["sedentary_over_limit_minutes"] == 80
    assert data["metrics"]["event_counts"]["water"] == 1
    assert data["metrics"]["event_counts"]["meal"] == 1
    assert data["active_sitting"]["status"] == "active"
    assert data["latest_reminders"]["exercise"]["status"] == "scheduled"


def test_life_events_returns_selected_day_only(tmp_path) -> None:
    client, repo = _client(tmp_path)
    repo.create_event(
        event_type="water",
        occurred_at="2026-05-30T13:00:00+08:00",
        source="wechat",
        raw_text="喝水了",
    )
    repo.create_event(
        event_type="water",
        occurred_at="2026-05-29T13:00:00+08:00",
        source="wechat",
        raw_text="喝水了",
    )

    response = client.get("/api/life/events?date=2026-05-30")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["event_type"] == "water"
    assert data["items"][0]["raw_text"] == "喝水了"


def test_life_reminders_returns_selected_day_only(tmp_path) -> None:
    client, repo = _client(tmp_path)
    repo.create_reminder_log(
        reminder_type="water",
        scheduled_for="2026-05-30T10:00:00+08:00",
        status="sending",
        dedupe_key="water:2026-05-30T10:00",
        payload={"dry_run": False},
    )
    repo.create_reminder_log(
        reminder_type="water",
        scheduled_for="2026-05-29T10:00:00+08:00",
        status="sending",
        dedupe_key="water:2026-05-29T10:00",
        payload={"dry_run": False},
    )

    response = client.get("/api/life/reminders?date=2026-05-30")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["reminder_type"] == "water"
    assert data["items"][0]["payload_json"] == {"dry_run": False}


def test_life_summary_empty_database_has_stable_shape(tmp_path) -> None:
    client, _repo = _client(tmp_path)

    response = client.get("/api/life/summary?date=2026-05-30")

    assert response.status_code == 200
    data = response.json()
    assert data["metrics"]["sitting_total_minutes"] == 0
    assert data["metrics"]["sedentary_over_limit_minutes"] == 0
    assert data["metrics"]["event_counts"] == {}
    assert data["active_sitting"] is None
    assert data["latest_reminders"] == {}
