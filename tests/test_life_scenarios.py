"""Scenario-level coverage for Life Assistant P1 flows."""

from __future__ import annotations

from agents.life_agent import ActivitySnapshot, LifeAgent
from db.life_repo import LifeRepo
from db.life_schema import init_life_db


def _agent(tmp_path) -> LifeAgent:
    conn = init_life_db(str(tmp_path / "life.db"))
    return LifeAgent(LifeRepo(conn))


def test_life_recording_scenarios_cover_sleep_meal_water_and_exercise(tmp_path) -> None:
    agent = _agent(tmp_path)

    wake = agent.record_life_event(
        "中午13点半醒的",
        received_at="2026-05-30T17:35:54+08:00",
    )
    sleep = agent.record_life_event(
        "今天凌晨三点半睡的",
        received_at="2026-05-30T17:36:41+08:00",
    )
    meal = agent.record_life_event(
        "午饭吃了腰果饼干",
        received_at="2026-05-30T12:20:00+08:00",
    )
    water = agent.record_life_event(
        "喝水了",
        received_at="2026-05-30T16:00:00+08:00",
    )
    planned_exercise = agent.record_life_event(
        "我打算下午6点练八段锦。",
        received_at="2026-05-30T17:37:49+08:00",
    )
    completed_exercise = agent.record_life_event(
        "跑步 30 分钟",
        received_at="2026-05-30T19:00:00+08:00",
    )

    updated_wake = agent.repo.get_event(wake["event"]["id"])
    assert updated_wake["duration_minutes"] == 600
    assert sleep["status"] == "recorded"
    assert meal["event"]["event_type"] == "meal"
    assert meal["event"]["payload_json"]["content"] == "腰果饼干"
    assert water["event"]["event_type"] == "water"
    assert planned_exercise["scheduled_for"] == "2026-05-30T18:00:00+08:00"
    assert completed_exercise["event"]["event_type"] == "exercise"
    assert completed_exercise["event"]["duration_minutes"] == 30


def test_life_sedentary_scenario_requires_activity_break(tmp_path) -> None:
    agent = _agent(tmp_path)

    first_tick = agent.life_tick(
        now="2026-05-30T09:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=10, available=True),
        dry_run=True,
    )
    alert_tick = agent.life_tick(
        now="2026-05-30T10:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=30, available=True),
        dry_run=True,
    )
    break_tick = agent.life_tick(
        now="2026-05-30T10:20:00+08:00",
        activity=ActivitySnapshot(idle_seconds=360, available=True),
        dry_run=True,
    )

    break_row = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'sedentary_break'"
    ).fetchone()
    assert first_tick["selected"] is None
    assert alert_tick["selected"]["reminder_type"] == "sedentary"
    assert break_tick["selected"] is None
    assert break_row["duration_minutes"] == 80
