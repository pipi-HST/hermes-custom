"""Tests for read-only Life Dashboard API routes."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.main as api_main
from api.routes.life import create_life_router
from db.life_repo import LifeRepo
from db.life_schema import init_life_db


def _client(tmp_path, *, fresh_connections: bool = False) -> tuple[TestClient, LifeRepo]:
    db_path = str(tmp_path / "life.db")
    conn = init_life_db(db_path)
    app = FastAPI()
    if fresh_connections:
        app.include_router(create_life_router(db_path=db_path), prefix="/api")
    else:
        app.include_router(create_life_router(conn), prefix="/api")
    return TestClient(app), LifeRepo(conn)


def test_life_dashboard_3010_origin_is_allowed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        api_main,
        "load_config",
        lambda _path: SimpleNamespace(db_path=str(tmp_path / "life.db"), vault_path=str(tmp_path)),
    )
    app = api_main.create_app()
    client = TestClient(app)

    response = client.options(
        "/api/life/summary",
        headers={
            "Origin": "http://localhost:3010",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3010"


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


def test_life_events_hides_superseded_records(tmp_path) -> None:
    client, repo = _client(tmp_path)
    repo.create_event(
        event_type="meal",
        occurred_at="2026-05-30T00:00:00+08:00",
        source="wechat",
        raw_text="午饭吃了腰果饼干",
        status="superseded",
        payload={"meal_slot": "lunch", "content": "腰果饼干"},
    )
    repo.create_event(
        event_type="meal",
        occurred_at="2026-05-30T00:00:00+08:00",
        source="wechat",
        raw_text="午饭吃了米饭和鸡蛋",
        payload={"meal_slot": "lunch", "content": "米饭和鸡蛋"},
    )

    response = client.get("/api/life/events?date=2026-05-30")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["payload_json"]["content"] == "米饭和鸡蛋"


def test_life_api_can_read_with_fresh_connections(tmp_path) -> None:
    client, repo = _client(tmp_path, fresh_connections=True)
    repo.create_event(
        event_type="water",
        occurred_at="2026-05-30T13:00:00+08:00",
        source="wechat",
        raw_text="喝水了",
    )

    response = client.get("/api/life/events?date=2026-05-30")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["event_type"] == "water"


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


def test_life_reviews_returns_daily_and_weekly_reviews(tmp_path) -> None:
    client, repo = _client(tmp_path)
    repo.create_daily_review(
        review_date="2026-05-30",
        summary="睡眠7小时；运动1次。",
        metrics={"sleep_minutes": 420, "exercise_count": 1},
        notes={"sleep": "稳定"},
    )
    repo.create_weekly_review(
        week_start="2026-05-25",
        week_end="2026-05-31",
        summary="这一周运动节奏有恢复。",
        labels={"exercise": "improving"},
        patterns={"sleep": "weekday_late"},
        recommendations=["把运动提前到下午"],
    )

    response = client.get("/api/life/reviews?date=2026-05-30")

    assert response.status_code == 200
    data = response.json()
    assert data["date"] == "2026-05-30"
    assert data["daily_review"]["review_date"] == "2026-05-30"
    assert data["daily_review"]["summary"] == "睡眠7小时；运动1次。"
    assert data["daily_review"]["metrics_json"] == {"sleep_minutes": 420, "exercise_count": 1}
    assert data["daily_review"]["notes_json"] == {"sleep": "稳定"}
    assert data["weekly_review"]["week_start"] == "2026-05-25"
    assert data["weekly_review"]["week_end"] == "2026-05-31"
    assert data["weekly_review"]["summary"] == "这一周运动节奏有恢复。"
    assert data["weekly_review"]["labels_json"] == {"exercise": "improving"}
    assert data["weekly_review"]["patterns_json"] == {"sleep": "weekday_late"}
    assert data["weekly_review"]["recommendations_json"] == ["把运动提前到下午"]


def test_life_reviews_returns_long_term_decisions(tmp_path) -> None:
    client, repo = _client(tmp_path)
    decision = repo.create_long_term_decision(
        content="未来三个月优先打磨 Life Assistant",
        created_at="2026-05-31T16:00:00+08:00",
        next_review_at="2026-06-30T16:00:00+08:00",
        review_interval_days=30,
    )
    repo.create_decision_review(
        decision_id=decision["id"],
        reviewed_at="2026-06-30T16:05:00+08:00",
        review_note="继续有效",
        outcome="kept",
        next_review_at="2026-07-30T16:05:00+08:00",
    )

    response = client.get("/api/life/reviews?date=2026-06-30")

    assert response.status_code == 200
    data = response.json()
    assert data["long_term_decisions"][0]["content"] == "未来三个月优先打磨 Life Assistant"
    assert data["long_term_decisions"][0]["latest_review"]["review_note"] == "继续有效"


def test_life_reviews_empty_database_has_stable_shape(tmp_path) -> None:
    client, _repo = _client(tmp_path)

    response = client.get("/api/life/reviews?date=2026-05-30")

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "date": "2026-05-30",
        "daily_review": None,
        "weekly_review": None,
        "long_term_decisions": [],
    }


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
