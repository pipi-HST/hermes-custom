"""Tests for read-only Life Dashboard API routes."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.main as api_main
from api.routes.life import create_life_router
from db.life_repo import LifeRepo
from db.life_schema import init_life_db


def _client(
    tmp_path,
    *,
    fresh_connections: bool = False,
    cron_jobs_path: str | None = None,
    life_log_path: str | None = None,
) -> tuple[TestClient, LifeRepo]:
    db_path = str(tmp_path / "life.db")
    conn = init_life_db(db_path)
    app = FastAPI()
    if fresh_connections:
        app.include_router(
            create_life_router(
                db_path=db_path,
                cron_jobs_path=cron_jobs_path,
                life_log_path=life_log_path,
            ),
            prefix="/api",
        )
    else:
        app.include_router(
            create_life_router(
                conn,
                cron_jobs_path=cron_jobs_path,
                life_log_path=life_log_path,
            ),
            prefix="/api",
        )
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


def test_life_memories_returns_items_observations_and_hits(tmp_path) -> None:
    client, repo = _client(tmp_path)
    observation = repo.create_memory_observation(
        domain="life.meal",
        observation_type="meal_pattern",
        content="最近14天午餐有3次记录。",
        observed_at="2026-06-01T10:00:00+08:00",
        source="event_stats",
        evidence={"count": 3},
    )
    item = repo.upsert_memory_item(
        domain="life.meal",
        memory_type="pattern",
        status="weak",
        content="午餐常见记录时间约13:20。",
        confidence="medium",
        source="event_stats",
        effective_from="2026-06-01T10:00:00+08:00",
        evidence={"observation_id": observation["id"]},
    )
    repo.record_memory_hit(
        memory_id=item["id"],
        used_by="life_tick",
        target_id="reminder-1",
        used_at="2026-06-01T10:05:00+08:00",
        effect={"reminder_type": "meal"},
    )

    response = client.get("/api/life/memories")

    assert response.status_code == 200
    data = response.json()
    assert data["items"][0]["content"] == "午餐常见记录时间约13:20。"
    assert data["observations"][0]["id"] == observation["id"]
    assert data["hits"][0]["memory_id"] == item["id"]


def test_life_decision_traces_returns_selected_day_only(tmp_path) -> None:
    client, repo = _client(tmp_path)
    repo.create_decision_trace(
        tick_at="2026-06-01T10:00:00+08:00",
        status="selected",
        selected_reminder_type="water",
        selected_reminder_id="reminder-1",
        context={"date": "2026-06-01"},
        candidates=[{"reminder_type": "water", "reason": "喝水提醒到点"}],
        memory_influences=[{"memory_id": "m1", "effect": "context_only"}],
        decision_reason="喝水提醒到点。",
        warnings=["mac_activity_unavailable"],
    )
    repo.create_decision_trace(
        tick_at="2026-05-31T10:00:00+08:00",
        status="idle",
        context={"date": "2026-05-31"},
    )

    response = client.get("/api/life/decision-traces?date=2026-06-01")

    assert response.status_code == 200
    data = response.json()
    assert data["date"] == "2026-06-01"
    assert data["total"] == 1
    assert data["items"][0]["selected_reminder_type"] == "water"
    assert data["items"][0]["candidates_json"][0]["reminder_type"] == "water"
    assert data["items"][0]["memory_influences_json"][0]["memory_id"] == "m1"
    assert data["items"][0]["warnings_json"] == ["mac_activity_unavailable"]


def test_life_week_returns_weekly_metrics_trends_and_records(tmp_path) -> None:
    client, repo = _client(tmp_path)
    repo.create_event(
        event_type="sleep_end",
        occurred_at="2026-05-27T08:00:00+08:00",
        duration_minutes=420,
        source="wechat",
        payload={"wake_date": "2026-05-27"},
    )
    repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-28T18:00:00+08:00",
        duration_minutes=30,
        source="wechat",
        payload={"exercise_type": "八段锦"},
    )
    repo.create_event(
        event_type="meal",
        occurred_at="2026-05-28T12:30:00+08:00",
        source="wechat",
        raw_text="午饭吃了牛肉饭",
        payload={"meal_slot": "lunch", "content": "牛肉饭"},
    )
    repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-29T09:00:00+08:00",
        ended_at="2026-05-29T10:20:00+08:00",
        duration_minutes=80,
        source="mac_activity",
        status="auto_resolved",
    )
    repo.create_weekly_review(
        week_start="2026-05-25",
        week_end="2026-05-31",
        summary="本周要减少连续久坐。",
        labels={"sedentary": "偏高"},
        patterns={"exercise_days": 1},
        recommendations=["先把午后运动固定下来"],
        headline="久坐偏高，运动偏少",
        report_text="总体：本周久坐偏高，运动记录偏少。建议：下周先固定一次午后运动。",
        evidence=[{"type": "sedentary", "text": "05-29 久坐80分钟"}],
        delivery={"wechat": "sent", "feishu": "pending"},
    )

    response = client.get("/api/life/week?date=2026-05-30")

    assert response.status_code == 200
    data = response.json()
    assert data["week_start"] == "2026-05-25"
    assert data["week_end"] == "2026-05-31"
    assert data["metrics"]["sleep_days"] == 1
    assert data["metrics"]["exercise_days"] == 1
    assert data["metrics"]["sedentary_over_limit_minutes"] == 80
    assert len(data["trend_rows"]) == 7
    assert data["record_groups"]["meal"][0]["raw_text"] == "午饭吃了牛肉饭"
    assert data["weekly_review"]["headline"] == "久坐偏高，运动偏少"
    assert data["weekly_review"]["delivery_json"] == {"wechat": "sent", "feishu": "pending"}


def test_life_week_empty_database_has_stable_shape(tmp_path) -> None:
    client, _repo = _client(tmp_path)

    response = client.get("/api/life/week?date=2026-05-30")

    assert response.status_code == 200
    data = response.json()
    assert data["week_start"] == "2026-05-25"
    assert data["week_end"] == "2026-05-31"
    assert len(data["trend_rows"]) == 7
    assert data["metrics"]["sleep_days"] == 0
    assert data["record_groups"] == {
        "sleep": [],
        "meal": [],
        "exercise": [],
        "sedentary": [],
        "water": [],
    }
    assert data["weekly_review"] is None


def test_life_runtime_returns_cron_delivery_and_recent_triggers(tmp_path) -> None:
    jobs_path = tmp_path / "jobs.json"
    log_path = tmp_path / "life.log"
    jobs_path.write_text(
        """
        {
          "jobs": [
            {
              "name": "life-assistant-tick",
              "script": "life_tick_cron.py",
              "schedule_display": "every 1m",
              "enabled": true,
              "state": "scheduled",
              "last_run_at": "2026-05-30T10:19:00+08:00",
              "next_run_at": "2026-05-30T10:20:00+08:00",
              "last_status": "ok",
              "last_error": null,
              "last_delivery_error": "rate limited",
              "repeat": {"completed": 42}
            }
          ],
          "updated_at": "2026-05-30T10:19:00+08:00"
        }
        """,
        encoding="utf-8",
    )
    log_path.write_text(
        "\n".join(
            [
                '{"tick_time":"2026-05-30T10:18:00+08:00","status":"idle","selected_reminder":"","should_notify":false,"warnings":[]}',
                '{"tick_time":"2026-05-30T10:19:00+08:00","status":"selected","selected_reminder":"water","should_notify":true,"warnings":["notification_rate_limited"],"idle_seconds":12,"activity_available":true,"sedentary_state":"active","due_candidates":["water"]}',
            ]
        ),
        encoding="utf-8",
    )
    client, repo = _client(
        tmp_path,
        cron_jobs_path=str(jobs_path),
        life_log_path=str(log_path),
    )
    repo.create_reminder_log(
        reminder_type="water",
        scheduled_for="2026-05-30T10:10:00+08:00",
        status="failed",
        dedupe_key="water:failed",
        error="rate limited",
    )
    repo.create_reminder_log(
        reminder_type="sedentary",
        scheduled_for="2026-05-30T10:15:00+08:00",
        status="sending",
        dedupe_key="sedentary:sending",
    )

    response = client.get(
        "/api/life/runtime",
        params={"now": "2026-05-30T10:20:00+08:00"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["cron"]["found"] is True
    assert data["cron"]["health"] == "ok"
    assert data["cron"]["schedule_display"] == "every 1m"
    assert data["cron"]["last_run_age_seconds"] == 60
    assert data["delivery"]["status"] == "rate_limited"
    assert data["delivery"]["cooling_down"] is True
    assert data["delivery"]["cooldown_until"] == "2026-05-30T10:40:00+08:00"
    assert data["delivery"]["recent_failed_count"] == 1
    assert data["delivery"]["recent_sending_count"] == 1
    assert data["last_tick"]["selected_reminder"] == "water"
    assert data["last_tick"]["warnings"] == ["notification_rate_limited"]
    assert data["recent_reminders"][0]["reminder_type"] == "sedentary"
    rules = {item["reminder_type"]: item for item in data["reminder_rules"]}
    assert rules["exercise"]["schedule_text"] == "每天 10:30 起；未回复时每 2 小时提醒一次"
    assert rules["exercise"]["next_due_at"] == "2026-05-30T10:30:00+08:00"
    assert rules["water"]["schedule_text"] == "每天 10:00、16:00"
    assert rules["water"]["planned_times"] == [
        "2026-05-30T10:00:00+08:00",
        "2026-05-30T16:00:00+08:00",
    ]
    assert rules["meal"]["enabled"] is True
    assert rules["meal"]["schedule_text"] == "每天 21:30 检查；当天无饮食记录时提醒补记"
    assert rules["sleep_record"]["enabled"] is True
    assert rules["sleep_record"]["schedule_text"] == "每天 13:00 检查；缺完整睡眠时长时提醒补记"
    assert rules["daily_review"]["next_due_at"] == "2026-05-31T00:00:00+08:00"
    assert rules["weekly_review"]["next_due_at"] == "2026-05-31T21:30:00+08:00"
    assert rules["sedentary"]["schedule_text"] == "连续坐姿 60 分钟后触发；之后每 20 分钟重复，直到离开 6 分钟"
    assert rules["water"]["today_status"] == "failed"
    assert rules["water"]["latest_log"]["status"] == "failed"


def test_life_runtime_missing_files_has_stable_shape(tmp_path) -> None:
    client, _repo = _client(
        tmp_path,
        cron_jobs_path=str(tmp_path / "missing_jobs.json"),
        life_log_path=str(tmp_path / "missing_life.log"),
    )

    response = client.get(
        "/api/life/runtime",
        params={"now": "2026-05-30T10:20:00+08:00"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["cron"]["found"] is False
    assert data["cron"]["health"] == "missing"
    assert data["delivery"]["status"] == "ok"
    assert data["last_tick"] is None
    assert data["recent_reminders"] == []


def test_life_runtime_uses_newest_tick_time_not_last_log_line(tmp_path) -> None:
    log_path = tmp_path / "life.log"
    log_path.write_text(
        "\n".join(
            [
                '{"tick_time":"2026-06-01T00:50:00+08:00","status":"idle","selected_reminder":"","should_notify":false,"warnings":[]}',
                '{"tick_time":"2026-05-30T23:31:00+08:00","status":"idle","selected_reminder":"","should_notify":false,"warnings":["old_manual_run"]}',
            ]
        ),
        encoding="utf-8",
    )
    client, _repo = _client(
        tmp_path,
        cron_jobs_path=str(tmp_path / "missing_jobs.json"),
        life_log_path=str(log_path),
    )

    response = client.get("/api/life/runtime")

    assert response.status_code == 200
    data = response.json()
    assert data["last_tick"]["tick_time"] == "2026-06-01T00:50:00+08:00"


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
