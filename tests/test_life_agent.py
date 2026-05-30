"""Tests for Life Assistant agent behavior."""

from __future__ import annotations

import pytest

from agents.life_agent import ActivitySnapshot, LifeAgent, LifeConfig
from agents.life_intent_parser import LifeIntent
from db.life_repo import LifeRepo
from db.life_schema import init_life_db


@pytest.fixture
def agent(tmp_path):
    conn = init_life_db(str(tmp_path / "life.db"))
    return LifeAgent(LifeRepo(conn))


def test_records_sleep_start_and_end_with_duration(agent: LifeAgent) -> None:
    start = agent.record_life_event("我睡了", received_at="2026-05-29T00:30:00+08:00")
    end = agent.record_life_event("我醒了", received_at="2026-05-29T08:00:00+08:00")

    assert start["status"] == "recorded"
    assert start["event"]["event_type"] == "sleep_start"
    assert end["status"] == "recorded"
    assert end["event"]["event_type"] == "sleep_end"
    assert end["event"]["duration_minutes"] == 450
    assert "7小时30分钟" in end["chat_preview"]


def test_records_sleep_session_from_single_message(agent: LifeAgent) -> None:
    result = agent.record_life_event(
        "昨晚1点睡，今天8点醒",
        received_at="2026-05-30T09:00:00+08:00",
    )

    events = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type IN ('sleep_start', 'sleep_end') ORDER BY occurred_at"
    ).fetchall()
    assert result["status"] == "recorded"
    assert result["event"]["event_type"] == "sleep_end"
    assert result["event"]["duration_minutes"] == 420
    assert events[0]["occurred_at"] == "2026-05-30T01:00:00+08:00"
    assert events[1]["occurred_at"] == "2026-05-30T08:00:00+08:00"


def test_records_wake_with_explicit_noon_time(agent: LifeAgent) -> None:
    agent.record_life_event("我睡了", received_at="2026-05-29T02:00:00+08:00")

    end = agent.record_life_event(
        "我中午13点半醒的",
        received_at="2026-05-29T13:40:00+08:00",
    )

    assert end["status"] == "recorded"
    assert end["event"]["event_type"] == "sleep_end"
    assert end["event"]["occurred_at"] == "2026-05-29T13:30:00+08:00"
    assert end["event"]["duration_minutes"] == 690
    assert "11小时30分钟" in end["chat_preview"]


def test_backfills_sleep_duration_when_start_is_recorded_after_wake(agent: LifeAgent) -> None:
    wake = agent.record_life_event(
        "中午13点半醒的",
        received_at="2026-05-30T17:35:54+08:00",
    )

    start = agent.record_life_event(
        "今天凌晨三点半睡的",
        received_at="2026-05-30T17:36:41+08:00",
    )

    updated_wake = agent.repo.get_event(wake["event"]["id"])
    assert start["status"] == "recorded"
    assert start["event"]["event_type"] == "sleep_start"
    assert updated_wake["duration_minutes"] == 600
    assert "10小时" in start["chat_preview"]


class FakeParser:
    def parse(self, message, *, received_at):
        if message == "我睡了":
            return LifeIntent(
                intent="record_event",
                event_type="sleep_start",
                occurred_at=received_at.isoformat(),
            )
        return LifeIntent(
            intent="record_event",
            event_type="sleep_end",
            occurred_at="2026-05-30T13:30:00+08:00",
        )


def test_life_agent_executes_structured_sleep_end_intent(tmp_path) -> None:
    conn = init_life_db(str(tmp_path / "life.db"))
    agent = LifeAgent(LifeRepo(conn), parser=FakeParser())
    agent.record_life_event("我睡了", received_at="2026-05-30T02:00:00+08:00")

    result = agent.record_life_event(
        "随便一种醒来表达",
        received_at="2026-05-30T13:40:00+08:00",
    )

    assert result["status"] == "recorded"
    assert result["event"]["event_type"] == "sleep_end"
    assert result["event"]["occurred_at"] == "2026-05-30T13:30:00+08:00"
    assert result["event"]["duration_minutes"] == 690


class IncompleteMealParser:
    def parse(self, message, *, received_at):
        return LifeIntent(
            intent="record_event",
            event_type="meal",
            occurred_at=received_at.isoformat(),
            payload={"meal_slot": "snack"},
        )


def test_life_agent_rejects_incomplete_structured_intent(tmp_path) -> None:
    conn = init_life_db(str(tmp_path / "life.db"))
    agent = LifeAgent(LifeRepo(conn), parser=IncompleteMealParser())

    result = agent.record_life_event(
        "刚补了一点东西",
        received_at="2026-05-30T15:10:00+08:00",
    )

    assert result["status"] == "rejected"
    assert result["event"] is None


def test_records_meal_with_inferred_slot(agent: LifeAgent) -> None:
    result = agent.record_life_event(
        "吃了牛肉饭",
        received_at="2026-05-29T12:10:00+08:00",
    )

    assert result["status"] == "recorded"
    assert result["event"]["event_type"] == "meal"
    assert result["event"]["payload_json"]["meal_slot"] == "lunch"
    assert result["event"]["payload_json"]["meal_slot_inferred"] is True


def test_records_exercise_unknown_when_details_missing(agent: LifeAgent) -> None:
    result = agent.record_life_event(
        "已运动",
        received_at="2026-05-29T18:00:00+08:00",
    )

    assert result["status"] == "recorded"
    assert result["event"]["event_type"] == "exercise"
    assert result["event"]["status"] == "completed_unknown"
    assert "可以之后补充" in result["chat_preview"]


def test_records_exercise_with_type_and_duration(agent: LifeAgent) -> None:
    result = agent.record_life_event(
        "跑步 30 分钟",
        received_at="2026-05-29T18:00:00+08:00",
    )

    assert result["status"] == "recorded"
    assert result["event"]["event_type"] == "exercise"
    assert result["event"]["duration_minutes"] == 30
    assert result["event"]["payload_json"]["exercise_type"] == "跑步"


def test_skips_today_exercise_reminder(agent: LifeAgent) -> None:
    result = agent.record_life_event(
        "今天不练了",
        received_at="2026-05-29T10:45:00+08:00",
    )

    assert result["status"] == "reminder_updated"
    assert result["event"] is None
    assert agent.repo.count_reminder_logs("exercise") == 1
    assert "今天不练" in result["chat_preview"]


def test_exercise_skip_stops_same_day_exercise_ticks(agent: LifeAgent) -> None:
    agent.record_life_event(
        "今天不练了",
        received_at="2026-05-29T10:45:00+08:00",
    )

    result = agent.life_tick(
        now="2026-05-29T12:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert result["selected"] is None


def test_reschedules_exercise_reminder(agent: LifeAgent) -> None:
    result = agent.record_life_event(
        "下午 4 点提醒我运动",
        received_at="2026-05-29T10:45:00+08:00",
    )

    assert result["status"] == "reminder_updated"
    assert result["event"] is None
    assert result["scheduled_for"] == "2026-05-29T16:00:00+08:00"
    assert agent.repo.count_reminder_logs("exercise") == 1


def test_planned_exercise_time_creates_scheduled_exercise_reminder(agent: LifeAgent) -> None:
    result = agent.record_life_event(
        "我打算下午6点练八段锦。",
        received_at="2026-05-30T17:37:49+08:00",
    )

    row = agent.repo.conn.execute(
        "SELECT * FROM life_reminder_logs WHERE reminder_type = 'exercise'"
    ).fetchone()
    assert result["status"] == "reminder_updated"
    assert result["scheduled_for"] == "2026-05-30T18:00:00+08:00"
    assert row["scheduled_for"] == "2026-05-30T18:00:00+08:00"
    assert "八段锦" in row["payload_json"]


def test_exercise_reschedule_delays_next_tick_until_scheduled_time(agent: LifeAgent) -> None:
    agent.record_life_event(
        "下午 4 点提醒我运动",
        received_at="2026-05-29T10:45:00+08:00",
    )

    early = agent.life_tick(
        now="2026-05-29T12:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )
    due = agent.life_tick(
        now="2026-05-29T16:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert early["selected"] is None
    assert due["selected"]["reminder_type"] == "exercise"


def test_exercise_reminder_repeats_only_after_repeat_interval(agent: LifeAgent) -> None:
    first = agent.life_tick(
        now="2026-05-29T10:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )
    too_soon = agent.life_tick(
        now="2026-05-29T11:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )
    due_again = agent.life_tick(
        now="2026-05-29T12:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert first["selected"]["reminder_type"] == "exercise"
    assert too_soon["selected"] is None
    assert due_again["selected"]["reminder_type"] == "exercise"


def test_undo_latest_life_event(agent: LifeAgent) -> None:
    event = agent.record_life_event(
        "喝水了",
        received_at="2026-05-29T10:00:00+08:00",
    )["event"]

    result = agent.record_life_event(
        "撤销上一条",
        received_at="2026-05-29T10:10:00+08:00",
    )

    assert result["status"] == "undone"
    assert agent.repo.get_event(event["id"])["status"] == "undone"


def test_rejects_manual_status_query_without_life_event(agent: LifeAgent) -> None:
    result = agent.record_life_event(
        "我今天坐了多久？",
        received_at="2026-05-29T18:00:00+08:00",
    )

    assert result["status"] == "rejected"
    assert result["event"] is None
    assert "定时复盘" in result["chat_preview"]


def test_health_consultation_gets_safety_boundary_without_life_event(agent: LifeAgent) -> None:
    result = agent.record_life_event(
        "健康：我最近睡不好，是不是身体有问题？",
        received_at="2026-05-30T18:00:00+08:00",
    )

    assert result["status"] == "rejected"
    assert result["event"] is None
    assert "不能做医学诊断" in result["chat_preview"]


def test_sedentary_pause_suppresses_alert_but_keeps_sitting_window(agent: LifeAgent) -> None:
    paused = agent.record_life_event(
        "暂停久坐警报 2 小时",
        received_at="2026-05-30T09:30:00+08:00",
    )

    first_tick = agent.life_tick(
        now="2026-05-30T09:31:00+08:00",
        activity=ActivitySnapshot(idle_seconds=10, available=True),
        dry_run=True,
    )
    paused_tick = agent.life_tick(
        now="2026-05-30T10:40:00+08:00",
        activity=ActivitySnapshot(idle_seconds=30, available=True),
        dry_run=True,
    )
    resumed_tick = agent.life_tick(
        now="2026-05-30T11:31:00+08:00",
        activity=ActivitySnapshot(idle_seconds=30, available=True),
        dry_run=True,
    )

    active_window = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'sedentary_start'"
    ).fetchone()
    assert paused["status"] == "reminder_updated"
    assert first_tick["selected"] is None
    assert paused_tick["selected"]["reminder_type"] == "exercise"
    assert resumed_tick["selected"]["reminder_type"] == "sedentary"
    assert active_window is not None
    assert active_window["status"] == "active"


def test_sedentary_activity_reply_does_not_resolve_active_window(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-30T09:00:00+08:00",
        source="mac_activity",
        raw_text="",
        status="active",
    )

    result = agent.record_life_event(
        "已活动",
        received_at="2026-05-30T10:05:00+08:00",
    )

    active_window = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'sedentary_start'"
    ).fetchone()
    assert result["status"] == "rejected"
    assert "离开电脑超过5分钟" in result["chat_preview"]
    assert active_window["status"] == "active"


def test_life_tick_sends_sedentary_alert_before_exercise(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-29T09:30:00+08:00",
        source="mac_activity",
        raw_text="",
        status="active",
    )

    result = agent.life_tick(
        now="2026-05-29T10:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=60, available=True),
        dry_run=True,
    )

    assert result["selected"]["reminder_type"] == "sedentary"
    assert result["meta"]["should_notify"] is False
    assert "起来活动" in result["chat_preview"]


def test_life_tick_starts_sitting_window_without_alert_before_threshold(agent: LifeAgent) -> None:
    result = agent.life_tick(
        now="2026-05-29T09:31:00+08:00",
        activity=ActivitySnapshot(idle_seconds=60, available=True),
        dry_run=True,
    )

    assert result["selected"] is None
    row = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'sedentary_start'"
    ).fetchone()
    assert row is not None
    assert row["status"] == "active"


def test_life_tick_records_break_after_idle_exceeds_threshold(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-29T09:10:00+08:00",
        source="mac_activity",
        raw_text="",
        status="active",
    )

    result = agent.life_tick(
        now="2026-05-29T10:20:00+08:00",
        activity=ActivitySnapshot(idle_seconds=360, available=True),
        dry_run=True,
    )

    assert result["selected"] is None
    break_row = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'sedentary_break'"
    ).fetchone()
    assert break_row is not None
    assert break_row["duration_minutes"] == 70


def test_life_tick_continues_other_reminders_when_activity_unavailable(agent: LifeAgent) -> None:
    result = agent.life_tick(
        now="2026-05-29T10:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert result["selected"]["reminder_type"] == "exercise"
    assert "运动" in result["chat_preview"]
    assert result["warnings"] == ["mac_activity_unavailable"]


def test_life_tick_sends_water_reminder(agent: LifeAgent) -> None:
    result = agent.life_tick(
        now="2026-05-29T10:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert result["selected"]["reminder_type"] == "water"
    assert "喝水" in result["chat_preview"]


def test_life_tick_sends_sleep_reminder(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-29T12:00:00+08:00",
        source="wechat",
        raw_text="跑步 30 分钟",
    )

    result = agent.life_tick(
        now="2026-05-29T23:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert result["selected"]["reminder_type"] == "sleep"
    assert "睡" in result["chat_preview"]


def test_life_tick_sends_daily_review(agent: LifeAgent) -> None:
    result = agent.life_tick(
        now="2026-05-30T00:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert result["selected"]["reminder_type"] == "daily_review"
    assert "复盘" in result["chat_preview"]


def test_life_tick_marks_sending_only_when_notification_will_send(tmp_path) -> None:
    conn = init_life_db(str(tmp_path / "life.db"))
    agent = LifeAgent(LifeRepo(conn), config=LifeConfig(notifications_enabled=False))

    result = agent.life_tick(
        now="2026-05-29T10:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )

    row = conn.execute("SELECT status FROM life_reminder_logs LIMIT 1").fetchone()
    assert result["meta"]["should_notify"] is False
    assert row["status"] == "scheduled"
