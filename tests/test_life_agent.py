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


def test_sleep_session_updates_existing_same_wake_date_record(agent: LifeAgent) -> None:
    rough_start = agent.record_life_event(
        "我睡了",
        received_at="2026-05-30T03:00:00+08:00",
    )["event"]
    rough_end = agent.record_life_event(
        "中午13点半醒的",
        received_at="2026-05-30T13:40:00+08:00",
    )["event"]

    result = agent.record_life_event(
        "今天凌晨三点半睡，中午13点半醒",
        received_at="2026-05-30T17:36:41+08:00",
    )

    events = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type IN ('sleep_start', 'sleep_end') ORDER BY occurred_at"
    ).fetchall()
    updated_start = agent.repo.get_event(rough_start["id"])
    updated_end = agent.repo.get_event(rough_end["id"])
    assert result["status"] == "modified"
    assert result["event"]["id"] == rough_end["id"]
    assert len(events) == 2
    assert updated_start["occurred_at"] == "2026-05-30T03:30:00+08:00"
    assert updated_end["occurred_at"] == "2026-05-30T13:30:00+08:00"
    assert updated_end["duration_minutes"] == 600


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


def test_records_explicit_slot_meal_without_using_chat_time_as_meal_time(agent: LifeAgent) -> None:
    result = agent.record_life_event(
        "午饭吃了腰果饼干",
        received_at="2026-05-30T16:32:00+08:00",
    )

    assert result["status"] == "recorded"
    assert result["event"]["event_type"] == "meal"
    assert result["event"]["occurred_at"] == "2026-05-30T00:00:00+08:00"
    assert result["event"]["payload_json"]["meal_time_precision"] == "missing"
    assert result["event"]["payload_json"]["recorded_at"] == "2026-05-30T16:32:00+08:00"
    assert "大概几点吃的" in result["chat_preview"]


def test_same_day_same_meal_slot_overwrites_existing_meal(agent: LifeAgent) -> None:
    first = agent.record_life_event(
        "午饭吃了腰果饼干",
        received_at="2026-05-30T16:32:00+08:00",
    )
    second = agent.record_life_event(
        "午饭吃了米饭和鸡蛋",
        received_at="2026-05-30T19:06:00+08:00",
    )

    rows = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'meal'"
    ).fetchall()
    assert len(rows) == 1
    assert second["event"]["id"] == first["event"]["id"]
    assert second["event"]["payload_json"]["content"] == "米饭和鸡蛋"
    assert second["event"]["payload_json"]["meal_time_precision"] == "missing"
    assert second["event"]["payload_json"]["recorded_at"] == "2026-05-30T19:06:00+08:00"


def test_named_drink_supplements_existing_meal_instead_of_water(agent: LifeAgent) -> None:
    first = agent.record_life_event(
        "中午吃了面包",
        received_at="2026-05-31T12:05:00+08:00",
    )
    second = agent.record_life_event(
        "中午还喝了茉莉乌龙茶",
        received_at="2026-05-31T12:30:00+08:00",
    )

    meal_rows = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'meal'"
    ).fetchall()
    water_rows = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'water'"
    ).fetchall()

    assert first["event"]["event_type"] == "meal"
    assert second["event"]["id"] == first["event"]["id"]
    assert len(meal_rows) == 1
    assert len(water_rows) == 0
    assert second["event"]["payload_json"]["content"] == "面包、茉莉乌龙茶"
    assert second["event"]["payload_json"]["meal_item_kind"] == "beverage"
    assert second["event"]["raw_text"] == "中午吃了面包；中午还喝了茉莉乌龙茶"
    assert "已补充午餐：面包、茉莉乌龙茶" in second["chat_preview"]


def test_meal_content_update_preserves_existing_exact_meal_time(agent: LifeAgent) -> None:
    first = agent.record_life_event(
        "12点半午饭吃了腰果饼干",
        received_at="2026-05-30T16:32:00+08:00",
    )
    second = agent.record_life_event(
        "午饭吃了米饭和鸡蛋",
        received_at="2026-05-30T19:06:00+08:00",
    )

    assert second["event"]["id"] == first["event"]["id"]
    assert second["event"]["occurred_at"] == "2026-05-30T12:30:00+08:00"
    assert second["event"]["payload_json"]["content"] == "米饭和鸡蛋"
    assert second["event"]["payload_json"]["meal_time_precision"] == "exact"


def test_meal_append_to_existing_exact_time_does_not_ask_for_time(agent: LifeAgent) -> None:
    first = agent.record_life_event(
        "下午两点午饭吃了面包",
        received_at="2026-05-31T19:06:00+08:00",
    )

    second = agent.record_life_event(
        "中午还喝了茉莉乌龙茶",
        received_at="2026-05-31T19:37:00+08:00",
    )

    assert second["event"]["id"] == first["event"]["id"]
    assert second["event"]["occurred_at"] == "2026-05-31T14:00:00+08:00"
    assert second["event"]["payload_json"]["meal_time_precision"] == "exact"
    assert "大概几点" not in second["chat_preview"]


def test_follow_up_time_updates_latest_pending_meal(agent: LifeAgent) -> None:
    meal = agent.record_life_event(
        "午饭吃了腰果饼干",
        received_at="2026-05-30T16:32:00+08:00",
    )

    result = agent.record_life_event(
        "12点半",
        received_at="2026-05-30T16:33:00+08:00",
    )

    rows = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'meal'"
    ).fetchall()
    updated = agent.repo.get_event(meal["event"]["id"])
    assert len(rows) == 1
    assert result["status"] == "modified"
    assert result["event"]["id"] == meal["event"]["id"]
    assert updated["occurred_at"] == "2026-05-30T12:30:00+08:00"
    assert updated["payload_json"]["meal_time_precision"] == "exact"


def test_bare_follow_up_time_uses_pending_dinner_context(agent: LifeAgent) -> None:
    meal = agent.record_life_event(
        "晚饭吃了馄饨和烤肠和桂花糕",
        received_at="2026-06-01T21:00:00+08:00",
    )

    result = agent.record_life_event(
        "七点吃的",
        received_at="2026-06-01T21:05:00+08:00",
    )

    updated = agent.repo.get_event(meal["event"]["id"])
    assert result["status"] == "modified"
    assert result["event"]["id"] == meal["event"]["id"]
    assert updated["occurred_at"] == "2026-06-01T19:00:00+08:00"
    assert updated["payload_json"]["meal_time_precision"] == "exact"
    assert "19:00" in result["chat_preview"]


def test_meal_slot_time_phrase_updates_latest_pending_meal_without_changing_content(agent: LifeAgent) -> None:
    meal = agent.record_life_event(
        "中午吃了面包",
        received_at="2026-05-31T12:05:00+08:00",
    )
    agent.record_life_event(
        "中午还吃了奶酪馅曲奇",
        received_at="2026-05-31T15:12:56+08:00",
    )

    result = agent.record_life_event(
        "午餐时间是下午两点",
        received_at="2026-05-31T15:13:29+08:00",
    )

    rows = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'meal'"
    ).fetchall()
    updated = agent.repo.get_event(meal["event"]["id"])
    assert len(rows) == 1
    assert result["status"] == "modified"
    assert result["event"]["id"] == meal["event"]["id"]
    assert updated["occurred_at"] == "2026-05-31T14:00:00+08:00"
    assert updated["payload_json"]["content"] == "面包、奶酪馅曲奇"
    assert updated["payload_json"]["meal_time_precision"] == "exact"


def test_bare_meal_time_without_pending_context_does_not_modify_exact_meal(agent: LifeAgent) -> None:
    meal = agent.record_life_event(
        "下午两点午饭吃了面包",
        received_at="2026-05-31T19:06:00+08:00",
    )["event"]

    result = agent.record_life_event(
        "下午三点",
        received_at="2026-05-31T19:10:00+08:00",
    )

    updated = agent.repo.get_event(meal["id"])
    assert result["status"] == "rejected"
    assert updated["occurred_at"] == "2026-05-31T14:00:00+08:00"


def test_explicit_meal_time_change_updates_existing_exact_meal(agent: LifeAgent) -> None:
    meal = agent.record_life_event(
        "下午两点午饭吃了面包",
        received_at="2026-05-31T19:06:00+08:00",
    )["event"]

    result = agent.record_life_event(
        "午饭时间改成下午三点",
        received_at="2026-05-31T19:10:00+08:00",
    )

    updated = agent.repo.get_event(meal["id"])
    assert result["status"] == "modified"
    assert result["event"]["id"] == meal["id"]
    assert updated["occurred_at"] == "2026-05-31T15:00:00+08:00"
    assert updated["payload_json"]["meal_time_precision"] == "exact"


def test_records_exercise_unknown_when_details_missing(agent: LifeAgent) -> None:
    result = agent.record_life_event(
        "已运动",
        received_at="2026-05-29T18:00:00+08:00",
    )

    assert result["status"] == "recorded"
    assert result["event"]["event_type"] == "exercise"
    assert result["event"]["status"] == "completed_unknown"
    assert "可以之后补充" in result["chat_preview"]


def test_exercise_details_after_completed_unknown_update_existing_event(agent: LifeAgent) -> None:
    first = agent.record_life_event(
        "已运动",
        received_at="2026-05-29T18:00:00+08:00",
    )["event"]

    result = agent.record_life_event(
        "八段锦 20 分钟",
        received_at="2026-05-29T18:10:00+08:00",
    )

    rows = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'exercise'"
    ).fetchall()
    updated = agent.repo.get_event(first["id"])
    assert result["status"] == "modified"
    assert result["event"]["id"] == first["id"]
    assert len(rows) == 1
    assert updated["status"] == "recorded"
    assert updated["duration_minutes"] == 20
    assert updated["payload_json"]["exercise_type"] == "八段锦"


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

    assert (result["selected"] or {}).get("reminder_type") != "sedentary"


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


def test_rescheduling_exercise_supersedes_previous_same_day_plan(agent: LifeAgent) -> None:
    first = agent.record_life_event(
        "我打算下午6点练八段锦。",
        received_at="2026-05-30T12:00:00+08:00",
    )

    second = agent.record_life_event(
        "改到晚上8点练八段锦",
        received_at="2026-05-30T12:10:00+08:00",
    )

    rows = agent.repo.conn.execute(
        "SELECT * FROM life_reminder_logs WHERE reminder_type = 'exercise' ORDER BY scheduled_for"
    ).fetchall()
    assert first["status"] == "reminder_updated"
    assert second["status"] == "reminder_updated"
    assert len(rows) == 2
    assert rows[0]["status"] == "superseded"
    assert rows[1]["status"] == "scheduled"
    assert rows[1]["scheduled_for"] == "2026-05-30T20:00:00+08:00"


def test_completed_exercise_marks_same_day_plan_completed(agent: LifeAgent) -> None:
    agent.record_life_event(
        "我打算晚上10点练八段锦",
        received_at="2026-05-31T19:23:00+08:00",
    )

    result = agent.record_life_event(
        "已运动",
        received_at="2026-05-31T19:24:00+08:00",
    )

    row = agent.repo.conn.execute(
        "SELECT * FROM life_reminder_logs WHERE reminder_type = 'exercise'"
    ).fetchone()
    assert result["status"] == "recorded"
    assert row["status"] == "completed"
    assert result["event"]["id"] in row["payload_json"]


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


def test_exercise_reminder_sending_log_throttles_next_tick(agent: LifeAgent) -> None:
    agent.config = LifeConfig(notifications_enabled=True)

    first = agent.life_tick(
        now="2026-05-30T19:29:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )
    too_soon = agent.life_tick(
        now="2026-05-30T19:40:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )

    assert first["selected"]["reminder_type"] == "exercise"
    assert first["meta"]["should_notify"] is True
    assert too_soon["selected"] is None


def test_exercise_reminder_throttles_after_sent_delivery(agent: LifeAgent) -> None:
    agent.config = LifeConfig(notifications_enabled=True)

    first = agent.life_tick(
        now="2026-05-30T10:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )
    agent.reconcile_delivery_result(
        completed_at="2026-05-30T10:30:30+08:00",
        delivery_error="",
    )
    too_soon_after_sent = agent.life_tick(
        now="2026-05-30T10:31:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )
    due_again = agent.life_tick(
        now="2026-05-30T12:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )

    assert first["selected"]["reminder_type"] == "exercise"
    assert first["meta"]["should_notify"] is True
    assert (too_soon_after_sent["selected"] or {}).get("reminder_type") != "exercise"
    assert due_again["selected"]["reminder_type"] == "exercise"


def test_reconcile_delivery_result_marks_sending_reminder_sent(agent: LifeAgent) -> None:
    agent.repo.create_reminder_log(
        reminder_type="water",
        scheduled_for="2026-05-30T10:00:00+08:00",
        status="sending",
        dedupe_key="water:2026-05-30T10:00",
    )

    result = agent.reconcile_delivery_result(
        completed_at="2026-05-30T10:00:30+08:00",
        delivery_error="",
    )

    row = agent.repo.conn.execute(
        "SELECT * FROM life_reminder_logs WHERE dedupe_key = 'water:2026-05-30T10:00'"
    ).fetchone()
    assert result["updated"] == 1
    assert row["status"] == "sent"
    assert row["sent_at"] == "2026-05-30T10:00:30+08:00"
    assert row["error"] == ""


def test_reconcile_delivery_result_marks_sending_reminder_failed(agent: LifeAgent) -> None:
    agent.repo.create_reminder_log(
        reminder_type="sleep",
        scheduled_for="2026-05-30T23:30:00+08:00",
        status="sending",
        dedupe_key="sleep:2026-05-30",
    )

    result = agent.reconcile_delivery_result(
        completed_at="2026-05-30T23:30:30+08:00",
        delivery_error="rate limited",
    )

    row = agent.repo.conn.execute(
        "SELECT * FROM life_reminder_logs WHERE dedupe_key = 'sleep:2026-05-30'"
    ).fetchone()
    assert result["updated"] == 1
    assert row["status"] == "failed"
    assert row["error"] == "rate limited"


def test_sleep_failed_delivery_does_not_retry_in_due_window(agent: LifeAgent) -> None:
    agent.config = LifeConfig(notifications_enabled=True)
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-30T18:00:00+08:00",
        source="wechat",
        status="completed_unknown",
    )

    first = agent.life_tick(
        now="2026-05-30T23:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )
    agent.reconcile_delivery_result(
        completed_at="2026-05-30T23:30:30+08:00",
        delivery_error="rate limited",
    )
    repeat = agent.life_tick(
        now="2026-05-30T23:31:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )

    assert first["selected"]["reminder_type"] == "sleep"
    assert (repeat["selected"] or {}).get("reminder_type") != "sleep"


def test_weixin_rate_limit_suppresses_notification_candidates_temporarily(agent: LifeAgent) -> None:
    agent.config = LifeConfig(notifications_enabled=True)
    agent.repo.create_reminder_log(
        reminder_type="sedentary",
        scheduled_for="2026-05-31T23:40:00+08:00",
        status="failed",
        dedupe_key="sedentary:2026-05-31T23:40",
        error="delivery error: Weixin send failed: iLink sendmessage rate limited",
    )
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-31T18:00:00+08:00",
        source="wechat",
        status="completed_unknown",
    )

    suppressed = agent.life_tick(
        now="2026-06-01T00:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )
    after_cooldown = agent.life_tick(
        now="2026-06-01T00:11:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )

    assert suppressed["selected"] is None
    assert suppressed["meta"]["should_notify"] is False
    assert "notification_rate_limited" in suppressed["warnings"]
    assert after_cooldown["selected"]["reminder_type"] == "daily_review"


def test_generic_rate_limited_delivery_suppresses_notification_candidates_temporarily(agent: LifeAgent) -> None:
    agent.config = LifeConfig(notifications_enabled=True)
    agent.repo.create_reminder_log(
        reminder_type="sedentary",
        scheduled_for="2026-05-31T23:40:00+08:00",
        status="failed",
        dedupe_key="sedentary:2026-05-31T23:40",
        error="rate limited",
    )
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-31T18:00:00+08:00",
        source="wechat",
        status="completed_unknown",
    )

    suppressed = agent.life_tick(
        now="2026-06-01T00:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )

    assert suppressed["selected"] is None
    assert suppressed["meta"]["should_notify"] is False
    assert "notification_rate_limited" in suppressed["warnings"]


def test_exercise_failed_delivery_gets_two_short_retries_then_waits(agent: LifeAgent) -> None:
    agent.config = LifeConfig(notifications_enabled=True)

    first = agent.life_tick(
        now="2026-05-30T10:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )
    agent.reconcile_delivery_result(
        completed_at="2026-05-30T10:30:30+08:00",
        delivery_error="temporary delivery timeout",
    )
    retry_one = agent.life_tick(
        now="2026-05-30T10:31:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )
    agent.reconcile_delivery_result(
        completed_at="2026-05-30T10:31:30+08:00",
        delivery_error="temporary delivery timeout",
    )
    retry_two = agent.life_tick(
        now="2026-05-30T10:32:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )
    agent.reconcile_delivery_result(
        completed_at="2026-05-30T10:32:30+08:00",
        delivery_error="temporary delivery timeout",
    )
    after_retries = agent.life_tick(
        now="2026-05-30T10:33:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )

    assert first["selected"]["reminder_type"] == "exercise"
    assert retry_one["selected"]["reminder_type"] == "exercise"
    assert retry_one["selected"]["payload"]["retry_count"] == 1
    assert retry_two["selected"]["reminder_type"] == "exercise"
    assert retry_two["selected"]["payload"]["retry_count"] == 2
    assert (after_retries["selected"] or {}).get("reminder_type") != "exercise"


def test_life_tick_handles_timezone_aware_exercise_reschedule_without_now(agent: LifeAgent) -> None:
    agent.record_life_event(
        "我晚上八点练",
        received_at="2026-05-30T19:51:18+08:00",
    )

    result = agent.life_tick(
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )

    assert result["status"] in {"selected", "idle"}


def test_sleep_reminder_notifies_only_once_per_day(agent: LifeAgent) -> None:
    agent.config = LifeConfig(notifications_enabled=True)
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-30T18:00:00+08:00",
        source="wechat",
        status="completed_unknown",
    )

    first = agent.life_tick(
        now="2026-05-30T23:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )
    repeated = agent.life_tick(
        now="2026-05-30T23:31:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )

    assert first["selected"]["reminder_type"] == "sleep"
    assert first["meta"]["should_notify"] is True
    assert (repeated["selected"] or {}).get("reminder_type") != "sleep"
    assert repeated["meta"]["should_notify"] is False
    assert agent.repo.count_reminder_logs("sleep") == 1


def test_water_reminder_notifies_only_once_per_due_time(agent: LifeAgent) -> None:
    agent.config = LifeConfig(notifications_enabled=True)
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-30T18:00:00+08:00",
        source="wechat",
        status="completed_unknown",
    )

    first = agent.life_tick(
        now="2026-05-30T10:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )
    repeated = agent.life_tick(
        now="2026-05-30T10:01:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )

    assert first["selected"]["reminder_type"] == "water"
    assert (repeated["selected"] or {}).get("reminder_type") != "water"
    assert repeated["meta"]["should_notify"] is False
    assert agent.repo.count_reminder_logs("water") == 1


def test_life_tick_records_decision_trace_without_changing_selection(agent: LifeAgent) -> None:
    result = agent.life_tick(
        now="2026-05-30T10:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    trace = agent.repo.list_decision_traces(limit=1)[0]
    assert result["selected"]["reminder_type"] == "water"
    assert trace["status"] == "selected"
    assert trace["selected_reminder_type"] == "water"
    assert trace["decision_reason"]
    assert any(item["reminder_type"] == "water" for item in trace["candidates_json"])
    assert trace["warnings_json"] == ["mac_activity_unavailable"]


def test_life_tick_records_memory_influence_in_decision_trace(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-06-01T08:00:00+08:00",
        source="wechat",
        status="completed_unknown",
    )
    memory = agent.repo.upsert_memory_item(
        domain="life.water",
        memory_type="pattern",
        status="weak",
        content="最近14天有3天记录喝水，平均每天约1次。",
        confidence="medium",
        source="event_stats",
        effective_from="2026-06-01T09:00:00+08:00",
        evidence={"water_days": 3},
    )

    agent.life_tick(
        now="2026-06-01T10:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    trace = agent.repo.list_decision_traces(limit=1)[0]
    assert trace["selected_reminder_type"] == "water"
    assert trace["memory_influences_json"][0]["memory_id"] == memory["id"]
    assert trace["memory_influences_json"][0]["effect"] == "context_only"


def test_sleep_record_reminder_prompts_when_last_night_sleep_is_missing(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-30T10:30:00+08:00",
        source="wechat",
        status="completed_unknown",
    )

    result = agent.life_tick(
        now="2026-05-30T13:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert result["selected"]["reminder_type"] == "sleep_record"
    assert "昨晚" in result["chat_preview"]
    assert "几点睡" in result["chat_preview"]


def test_sleep_record_reminder_skips_when_wake_day_has_duration(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-30T10:30:00+08:00",
        source="wechat",
        status="completed_unknown",
    )
    agent.repo.create_event(
        event_type="sleep_end",
        occurred_at="2026-05-30T08:00:00+08:00",
        source="wechat",
        duration_minutes=420,
        payload={"wake_date": "2026-05-30"},
    )

    result = agent.life_tick(
        now="2026-05-30T13:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert (result["selected"] or {}).get("reminder_type") != "sleep_record"


def test_meal_record_reminder_prompts_when_day_has_no_meals(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-30T10:30:00+08:00",
        source="wechat",
        status="completed_unknown",
    )

    result = agent.life_tick(
        now="2026-05-30T21:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert result["selected"]["reminder_type"] == "meal"
    assert "今天吃了什么" in result["chat_preview"]


def test_meal_record_reminder_skips_when_any_meal_exists(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-30T10:30:00+08:00",
        source="wechat",
        status="completed_unknown",
    )
    agent.repo.create_event(
        event_type="meal",
        occurred_at="2026-05-30T12:30:00+08:00",
        source="wechat",
        raw_text="午饭吃了面包",
        payload={"meal_slot": "lunch", "content": "面包"},
    )

    result = agent.life_tick(
        now="2026-05-30T21:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert (result["selected"] or {}).get("reminder_type") != "meal"


def test_daily_review_reminder_notifies_only_once_per_review_date(agent: LifeAgent) -> None:
    agent.config = LifeConfig(notifications_enabled=True)
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-30T18:00:00+08:00",
        source="wechat",
        status="completed_unknown",
    )

    first = agent.life_tick(
        now="2026-05-31T00:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )
    repeated = agent.life_tick(
        now="2026-05-31T00:01:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )

    assert first["selected"]["reminder_type"] == "daily_review"
    assert (repeated["selected"] or {}).get("reminder_type") != "daily_review"
    assert repeated["meta"]["should_notify"] is False
    assert agent.repo.count_reminder_logs("daily_review") == 1


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


def test_modifies_latest_meal_slot_within_window(agent: LifeAgent) -> None:
    event = agent.record_life_event(
        "午饭吃了牛肉饭",
        received_at="2026-05-30T12:10:00+08:00",
    )["event"]

    result = agent.record_life_event(
        "上一条改成加餐",
        received_at="2026-05-30T12:20:00+08:00",
    )

    updated = agent.repo.get_event(event["id"])
    assert result["status"] == "modified"
    assert result["event"]["id"] == event["id"]
    assert updated["event_type"] == "meal"
    assert updated["payload_json"]["meal_slot"] == "snack"
    assert updated["payload_json"]["content"] == "牛肉饭"
    assert "加餐" in result["chat_preview"]


def test_modifies_latest_meal_content_within_window(agent: LifeAgent) -> None:
    event = agent.record_life_event(
        "午饭吃了牛肉饭",
        received_at="2026-05-30T12:10:00+08:00",
    )["event"]

    result = agent.record_life_event(
        "上一条改成晚饭吃了面",
        received_at="2026-05-30T12:20:00+08:00",
    )

    updated = agent.repo.get_event(event["id"])
    assert result["status"] == "modified"
    assert result["event"]["id"] == event["id"]
    assert updated["payload_json"]["meal_slot"] == "dinner"
    assert updated["payload_json"]["content"] == "面"


def test_modifies_latest_exercise_details_within_window(agent: LifeAgent) -> None:
    event = agent.record_life_event(
        "已运动",
        received_at="2026-05-30T18:00:00+08:00",
    )["event"]

    result = agent.record_life_event(
        "上一条改成八段锦 20 分钟",
        received_at="2026-05-30T18:10:00+08:00",
    )

    updated = agent.repo.get_event(event["id"])
    rows = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'exercise'"
    ).fetchall()
    assert result["status"] == "modified"
    assert result["event"]["id"] == event["id"]
    assert len(rows) == 1
    assert updated["duration_minutes"] == 20
    assert updated["status"] == "recorded"
    assert updated["payload_json"]["exercise_type"] == "八段锦"


def test_modifies_latest_sleep_session_within_window(agent: LifeAgent) -> None:
    agent.record_life_event(
        "我睡了",
        received_at="2026-05-31T03:00:00+08:00",
    )
    end = agent.record_life_event(
        "中午13点醒",
        received_at="2026-05-31T13:05:00+08:00",
    )["event"]

    result = agent.record_life_event(
        "上一条改成凌晨三点半睡，中午13点半醒",
        received_at="2026-05-31T13:10:00+08:00",
    )

    events = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type IN ('sleep_start', 'sleep_end') ORDER BY occurred_at"
    ).fetchall()
    updated_end = agent.repo.get_event(end["id"])
    assert result["status"] == "modified"
    assert result["event"]["id"] == end["id"]
    assert len(events) == 2
    assert events[0]["occurred_at"] == "2026-05-31T03:30:00+08:00"
    assert updated_end["occurred_at"] == "2026-05-31T13:30:00+08:00"
    assert updated_end["duration_minutes"] == 600


def test_rejects_latest_record_modification_without_target(agent: LifeAgent) -> None:
    result = agent.record_life_event(
        "上一条改成加餐",
        received_at="2026-05-30T12:20:00+08:00",
    )

    assert result["status"] == "rejected"
    assert result["event"] is None
    assert "30 分钟内" in result["chat_preview"]


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
    assert "离开电脑超过6分钟" in result["chat_preview"]
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


def test_life_tick_does_not_let_repeated_sedentary_starve_first_exercise(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-29T09:00:00+08:00",
        source="mac_activity",
        raw_text="",
        status="active",
    )
    agent.repo.create_reminder_log(
        reminder_type="sedentary",
        scheduled_for="2026-05-29T10:30:00+08:00",
        status="sent",
        dedupe_key="sedentary:2026-05-29T10:30",
    )

    result = agent.life_tick(
        now="2026-05-29T10:50:00+08:00",
        activity=ActivitySnapshot(idle_seconds=60, available=True),
        dry_run=True,
    )

    assert result["selected"]["reminder_type"] == "exercise"


def test_life_tick_starts_sitting_window_without_alert_before_threshold(agent: LifeAgent) -> None:
    result = agent.life_tick(
        now="2026-05-29T09:31:00+08:00",
        activity=ActivitySnapshot(idle_seconds=60, available=True),
        dry_run=True,
    )

    assert (result["selected"] or {}).get("reminder_type") != "sedentary"
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
        activity=ActivitySnapshot(idle_seconds=660, available=True),
        dry_run=True,
    )

    assert (result["selected"] or {}).get("reminder_type") != "sedentary"
    break_row_raw = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'sedentary_break'"
    ).fetchone()
    break_row = agent.repo._event_row_to_dict(break_row_raw)
    assert break_row is not None
    assert break_row["duration_minutes"] == 65


def test_life_tick_closes_sedentary_window_at_last_input_time_after_long_idle(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-31T02:29:20+08:00",
        source="mac_activity",
        raw_text="",
        status="active",
    )

    result = agent.life_tick(
        now="2026-05-31T12:52:27+08:00",
        activity=ActivitySnapshot(idle_seconds=37400, available=True),
        dry_run=True,
    )

    updated = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'sedentary_start'"
    ).fetchone()
    break_row_raw = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'sedentary_break'"
    ).fetchone()
    break_row = agent.repo._event_row_to_dict(break_row_raw)
    assert (result["selected"] or {}).get("reminder_type") != "sedentary"
    assert updated["ended_at"] == "2026-05-31T02:35:07+08:00"
    assert updated["duration_minutes"] == 6
    assert break_row["occurred_at"] == "2026-05-31T02:35:07+08:00"


def test_life_tick_requires_six_minute_mac_interruption_to_close_sedentary_window(agent: LifeAgent) -> None:
    active = agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-29T09:00:00+08:00",
        source="mac_activity",
        raw_text="",
        status="active",
    )
    agent.repo.create_event(
        event_type="water",
        occurred_at="2026-05-29T10:00:00+08:00",
        source="wechat",
    )
    agent.repo.create_event(
        event_type="water",
        occurred_at="2026-05-29T10:00:00+08:00",
        source="wechat",
    )

    short_interruption = agent.life_tick(
        now="2026-05-29T10:05:00+08:00",
        activity=ActivitySnapshot(
            idle_seconds=0,
            available=True,
            sleeping=True,
            interruption_seconds=5 * 60,
            interruption_source="sleep",
        ),
        dry_run=True,
    )
    long_interruption = agent.life_tick(
        now="2026-05-29T10:07:00+08:00",
        activity=ActivitySnapshot(
            idle_seconds=0,
            available=True,
            sleeping=True,
            interruption_seconds=7 * 60,
            interruption_source="sleep",
        ),
        dry_run=True,
    )

    updated = agent.repo.get_event(active["id"])
    break_row_raw = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'sedentary_break'"
    ).fetchone()
    break_row = agent.repo._event_row_to_dict(break_row_raw)
    assert short_interruption["selected"] is None
    assert long_interruption["selected"] is None
    assert updated["status"] == "auto_resolved"
    assert updated["ended_at"] == "2026-05-29T10:06:00+08:00"
    assert break_row["payload_json"]["resolution_source"] == "mac_interrupted:sleep"


def test_life_tick_starts_new_sedentary_window_after_interruption_closed_previous_one(agent: LifeAgent) -> None:
    active = agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-29T09:00:00+08:00",
        source="mac_activity",
        raw_text="",
        status="active",
    )
    agent.repo.create_event(
        event_type="water",
        occurred_at="2026-05-29T10:00:00+08:00",
        source="wechat",
    )

    close_tick = agent.life_tick(
        now="2026-05-29T10:11:00+08:00",
        activity=ActivitySnapshot(
            idle_seconds=0,
            available=True,
            sleeping=True,
            interruption_seconds=11 * 60,
            interruption_source="sleep",
            interruption_started_at="2026-05-29T10:00:00+08:00",
            interruption_ended_at="2026-05-29T10:11:00+08:00",
        ),
        dry_run=True,
    )
    restart_tick = agent.life_tick(
        now="2026-05-29T10:12:00+08:00",
        activity=ActivitySnapshot(
            idle_seconds=0,
            available=True,
            sleeping=True,
            interruption_seconds=11 * 60,
            interruption_source="sleep",
            interruption_started_at="2026-05-29T10:00:00+08:00",
            interruption_ended_at="2026-05-29T10:11:00+08:00",
        ),
        dry_run=True,
    )
    follow_up_tick = agent.life_tick(
        now="2026-05-29T10:13:00+08:00",
        activity=ActivitySnapshot(
            idle_seconds=0,
            available=True,
            sleeping=True,
            interruption_seconds=11 * 60,
            interruption_source="sleep",
            interruption_started_at="2026-05-29T10:00:00+08:00",
            interruption_ended_at="2026-05-29T10:11:00+08:00",
        ),
        dry_run=True,
    )

    closed = agent.repo.get_event(active["id"])
    new_active = agent.repo.conn.execute(
        """SELECT * FROM life_events
           WHERE event_type = 'sedentary_start' AND status = 'active'
           ORDER BY occurred_at DESC LIMIT 1"""
    ).fetchone()
    assert close_tick["selected"] is None
    assert restart_tick["selected"] is None
    assert follow_up_tick["selected"] is None
    assert closed["status"] == "auto_resolved"
    assert new_active is not None
    assert new_active["occurred_at"] == "2026-05-29T10:12:00+08:00"
    assert new_active["ended_at"] is None


def test_sedentary_alert_repeats_only_after_repeat_interval(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-29T09:00:00+08:00",
        source="mac_activity",
        raw_text="",
        status="active",
    )

    first = agent.life_tick(
        now="2026-05-29T10:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=10, available=True),
        dry_run=False,
    )
    too_soon = agent.life_tick(
        now="2026-05-29T10:01:00+08:00",
        activity=ActivitySnapshot(idle_seconds=20, available=True),
        dry_run=False,
    )
    repeated = agent.life_tick(
        now="2026-05-29T10:20:00+08:00",
        activity=ActivitySnapshot(idle_seconds=30, available=True),
        dry_run=False,
    )

    assert first["selected"]["reminder_type"] == "sedentary"
    assert (too_soon["selected"] or {}).get("reminder_type") != "sedentary"
    assert repeated["selected"]["reminder_type"] == "sedentary"


def test_sedentary_alert_throttles_after_sent_delivery(agent: LifeAgent) -> None:
    agent.config = LifeConfig(notifications_enabled=True)
    agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-29T09:00:00+08:00",
        source="mac_activity",
        raw_text="",
        status="active",
    )

    first = agent.life_tick(
        now="2026-05-29T10:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=10, available=True),
        dry_run=False,
    )
    agent.reconcile_delivery_result(
        completed_at="2026-05-29T10:00:30+08:00",
        delivery_error="",
    )
    too_soon_after_sent = agent.life_tick(
        now="2026-05-29T10:01:00+08:00",
        activity=ActivitySnapshot(idle_seconds=20, available=True),
        dry_run=False,
    )
    repeated = agent.life_tick(
        now="2026-05-29T10:20:00+08:00",
        activity=ActivitySnapshot(idle_seconds=30, available=True),
        dry_run=False,
    )

    assert first["selected"]["reminder_type"] == "sedentary"
    assert (too_soon_after_sent["selected"] or {}).get("reminder_type") != "sedentary"
    assert repeated["selected"]["reminder_type"] == "sedentary"


def test_sedentary_alert_throttles_after_failed_delivery(agent: LifeAgent) -> None:
    agent.config = LifeConfig(notifications_enabled=True)
    agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-29T09:00:00+08:00",
        source="mac_activity",
        raw_text="",
        status="active",
    )

    first = agent.life_tick(
        now="2026-05-29T10:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=10, available=True),
        dry_run=False,
    )
    agent.reconcile_delivery_result(
        completed_at="2026-05-29T10:00:30+08:00",
        delivery_error="delivery error: rate limited",
    )
    too_soon_after_failed = agent.life_tick(
        now="2026-05-29T10:02:00+08:00",
        activity=ActivitySnapshot(idle_seconds=20, available=True),
        dry_run=False,
    )
    repeated = agent.life_tick(
        now="2026-05-29T10:10:00+08:00",
        activity=ActivitySnapshot(idle_seconds=30, available=True),
        dry_run=False,
    )
    after_cooldown = agent.life_tick(
        now="2026-05-29T10:31:00+08:00",
        activity=ActivitySnapshot(idle_seconds=30, available=True),
        dry_run=False,
    )

    assert first["selected"]["reminder_type"] == "sedentary"
    assert (too_soon_after_failed["selected"] or {}).get("reminder_type") != "sedentary"
    assert repeated["selected"] is None
    assert "notification_rate_limited" in repeated["warnings"]
    assert after_cooldown["selected"]["reminder_type"] == "sedentary"


def test_life_tick_handles_existing_naive_sedentary_window_with_aware_now(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-29T09:10:00",
        source="mac_activity",
        raw_text="",
        status="active",
    )

    result = agent.life_tick(
        now="2026-05-29T10:20:00+08:00",
        activity=ActivitySnapshot(idle_seconds=660, available=True),
        dry_run=True,
    )

    break_row = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'sedentary_break'"
    ).fetchone()
    assert result["selected"] is None
    assert break_row is not None
    assert break_row["duration_minutes"] == 65


def test_life_tick_does_not_close_sedentary_window_when_tick_time_is_before_window(agent: LifeAgent) -> None:
    active = agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-30T16:35:00",
        source="mac_activity",
        raw_text="",
        status="active",
    )

    result = agent.life_tick(
        now="2026-05-29T16:09:20+08:00",
        activity=ActivitySnapshot(idle_seconds=360, available=True),
        dry_run=True,
    )

    updated = agent.repo.get_event(active["id"])
    break_row = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'sedentary_break'"
    ).fetchone()
    assert (result["selected"] or {}).get("reminder_type") != "sedentary"
    assert updated["status"] == "active"
    assert updated["ended_at"] is None
    assert break_row is None


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


def test_life_tick_sends_water_reminder_when_cron_runs_after_due_minute(agent: LifeAgent) -> None:
    result = agent.life_tick(
        now="2026-05-29T10:09:20+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert result["selected"]["reminder_type"] == "water"
    assert result["selected"]["dedupe_key"] == "water:2026-05-29T10:00"


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


def test_life_tick_sends_sleep_reminder_when_cron_runs_after_due_minute(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-29T12:00:00+08:00",
        source="wechat",
        raw_text="跑步 30 分钟",
    )

    result = agent.life_tick(
        now="2026-05-29T23:39:20+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert result["selected"]["reminder_type"] == "sleep"
    assert result["selected"]["dedupe_key"] == "sleep:2026-05-29"


def test_life_tick_sends_daily_review(agent: LifeAgent) -> None:
    result = agent.life_tick(
        now="2026-05-30T00:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert result["selected"]["reminder_type"] == "daily_review"
    assert "复盘" in result["chat_preview"]


def test_life_tick_sends_daily_review_when_cron_runs_after_due_minute(agent: LifeAgent) -> None:
    result = agent.life_tick(
        now="2026-05-30T00:09:20+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert result["selected"]["reminder_type"] == "daily_review"
    assert result["selected"]["dedupe_key"] == "daily_review:2026-05-29"


def test_life_tick_exits_when_tick_lock_is_held(agent: LifeAgent) -> None:
    acquired = agent.repo.acquire_lock(
        name="life_tick",
        owner="other-worker",
        now="2026-05-30T10:00:00+08:00",
        ttl_seconds=50,
    )

    result = agent.life_tick(
        now="2026-05-30T10:00:10+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert acquired is True
    assert result["status"] == "locked"
    assert result["selected"] is None
    assert agent.repo.conn.execute("SELECT * FROM life_reminder_logs").fetchone() is None


def test_life_tick_stores_daily_review_when_selected(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="sleep_end",
        occurred_at="2026-05-30T08:00:00+08:00",
        duration_minutes=420,
        source="wechat",
        payload={"wake_date": "2026-05-30"},
    )
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-30T18:00:00+08:00",
        duration_minutes=30,
        source="wechat",
        payload={"exercise_type": "跑步"},
    )
    agent.repo.create_event(
        event_type="meal",
        occurred_at="2026-05-30T12:30:00+08:00",
        source="wechat",
        payload={"meal_slot": "lunch", "content": "牛肉饭"},
    )
    agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-30T09:00:00+08:00",
        ended_at="2026-05-30T10:20:00+08:00",
        duration_minutes=80,
        source="mac_activity",
        status="auto_resolved",
    )

    result = agent.life_tick(
        now="2026-05-31T00:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    row = agent.repo.conn.execute(
        "SELECT * FROM life_daily_reviews WHERE review_date = '2026-05-30'"
    ).fetchone()
    assert result["selected"]["reminder_type"] == "daily_review"
    assert row is not None
    assert "睡眠7小时" in row["summary"]
    assert "运动1次" in row["summary"]
    assert "每日轻复盘" in result["chat_preview"]


def test_life_tick_backfills_review_row_for_existing_daily_review_log(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-30T18:00:00+08:00",
        duration_minutes=30,
        source="wechat",
    )
    agent.repo.create_reminder_log(
        reminder_type="daily_review",
        scheduled_for="2026-05-31T00:00:10+08:00",
        status="sending",
        dedupe_key="daily_review:2026-05-30",
    )

    result = agent.life_tick(
        now="2026-05-31T00:16:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    row = agent.repo.conn.execute(
        "SELECT * FROM life_daily_reviews WHERE review_date = '2026-05-30'"
    ).fetchone()
    assert result["selected"] is None
    assert row is not None
    assert "运动1次" in row["summary"]


def test_life_tick_marks_daily_review_missed_after_cutoff(agent: LifeAgent) -> None:
    result = agent.life_tick(
        now="2026-05-31T02:01:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    row = agent.repo.conn.execute(
        "SELECT * FROM life_reminder_logs WHERE dedupe_key = 'daily_review:2026-05-30'"
    ).fetchone()
    assert result["selected"] is None
    assert row is not None
    assert row["status"] == "missed"


def test_life_tick_marks_weekly_review_missed_after_cutoff(agent: LifeAgent) -> None:
    result = agent.life_tick(
        now="2026-05-31T23:31:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    row = agent.repo.conn.execute(
        "SELECT * FROM life_reminder_logs WHERE dedupe_key = 'weekly_review:2026-05-25:2026-05-31'"
    ).fetchone()
    assert (result["selected"] or {}).get("reminder_type") != "weekly_review"
    assert row is not None
    assert row["status"] == "missed"


def test_life_tick_sends_and_stores_weekly_review(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-31T18:00:00+08:00",
        source="wechat",
        status="completed_unknown",
    )
    agent.repo.create_event(
        event_type="sleep_end",
        occurred_at="2026-05-30T08:00:00+08:00",
        duration_minutes=420,
        source="wechat",
        payload={"wake_date": "2026-05-30"},
    )

    result = agent.life_tick(
        now="2026-05-31T21:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    row = agent.repo.conn.execute("SELECT * FROM life_weekly_reviews").fetchone()
    assert result["selected"]["reminder_type"] == "weekly_review"
    assert row is not None
    assert row["week_start"] == "2026-05-25"
    assert row["week_end"] == "2026-05-31"
    assert "周复盘" in result["chat_preview"]


def test_weekly_review_is_concise_and_evidence_based(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="sleep_end",
        occurred_at="2026-05-27T08:00:00+08:00",
        duration_minutes=420,
        source="wechat",
        payload={"wake_date": "2026-05-27"},
    )
    agent.repo.create_event(
        event_type="meal",
        occurred_at="2026-05-28T12:30:00+08:00",
        source="wechat",
        raw_text="午饭吃了牛肉饭",
        payload={"meal_slot": "lunch", "content": "牛肉饭"},
    )
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-31T18:00:00+08:00",
        duration_minutes=30,
        source="wechat",
        payload={"exercise_type": "八段锦"},
    )
    agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-29T09:00:00+08:00",
        ended_at="2026-05-29T11:10:00+08:00",
        duration_minutes=130,
        source="mac_activity",
        status="auto_resolved",
    )

    result = agent.life_tick(
        now="2026-05-31T21:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    review = result["selected"]["review"]
    row = agent.repo.get_weekly_review(week_start="2026-05-25", week_end="2026-05-31")
    assert len(review["report_text"]) <= 600
    assert "总体" in review["report_text"]
    assert "建议" in review["report_text"]
    assert review["evidence"]
    assert row is not None
    assert row["report_text"] == review["report_text"]
    assert row["evidence_json"] == review["evidence"]


def test_long_term_decision_without_review_time_asks_follow_up(agent: LifeAgent) -> None:
    result = agent.record_life_event(
        "长期决定：未来三个月优先打磨 Life Assistant",
        received_at="2026-05-31T16:00:00+08:00",
    )

    row = agent.repo.conn.execute("SELECT * FROM life_long_term_decisions").fetchone()
    assert result["status"] == "decision_needs_review_time"
    assert row is not None
    assert row["content"] == "未来三个月优先打磨 Life Assistant"
    assert row["status"] == "pending_review_time"
    assert row["next_review_at"] == ""
    assert "下一次复盘" in result["chat_preview"]


def test_duplicate_long_term_decision_reuses_pending_row(agent: LifeAgent) -> None:
    first = agent.record_life_event(
        "长期决定：未来三个月优先打磨 Life Assistant",
        received_at="2026-05-31T16:00:00+08:00",
    )

    second = agent.record_life_event(
        "长期决定：未来三个月优先打磨 Life Assistant",
        received_at="2026-05-31T16:05:00+08:00",
    )

    rows = agent.repo.conn.execute("SELECT * FROM life_long_term_decisions").fetchall()
    assert len(rows) == 1
    assert second["decision"]["id"] == first["decision"]["id"]
    assert second["status"] == "decision_needs_review_time"


class LongTermDecisionWithTimeParser:
    def parse(self, message, *, received_at):
        return LifeIntent(
            intent="record_long_term_decision",
            payload={
                "content": "未来三个月优先打磨 Life Assistant",
                "next_review_at": "2026-06-15T20:00:00+08:00",
            },
        )


def test_records_long_term_decision_with_user_specified_review_time(tmp_path) -> None:
    conn = init_life_db(str(tmp_path / "life.db"))
    agent = LifeAgent(LifeRepo(conn), parser=LongTermDecisionWithTimeParser())

    result = agent.record_life_event(
        "长期决定：未来三个月优先打磨 Life Assistant，下个月15号晚上8点复盘",
        received_at="2026-05-31T16:00:00+08:00",
    )

    row = agent.repo.conn.execute("SELECT * FROM life_long_term_decisions").fetchone()
    assert result["status"] == "decision_recorded"
    assert row is not None
    assert row["content"] == "未来三个月优先打磨 Life Assistant"
    assert row["status"] == "active"
    assert row["next_review_at"] == "2026-06-15T20:00:00+08:00"
    assert "06-15" in result["chat_preview"]


def test_follow_up_review_time_activates_pending_long_term_decision(agent: LifeAgent) -> None:
    decision = agent.record_life_event(
        "长期决定：未来三个月优先打磨 Life Assistant",
        received_at="2026-05-31T16:00:00+08:00",
    )

    result = agent.record_life_event(
        "6月15日晚上8点复盘",
        received_at="2026-05-31T17:53:46+08:00",
    )

    updated = agent.repo.get_long_term_decision(decision["decision"]["id"])
    assert result["status"] == "decision_recorded"
    assert updated["status"] == "active"
    assert updated["next_review_at"] == "2026-06-15T20:00:00+08:00"


def test_long_term_review_time_follow_up_uses_active_context_not_latest_pending(agent: LifeAgent) -> None:
    first = agent.record_life_event(
        "长期决定：未来三个月优先打磨 Life Assistant",
        received_at="2026-05-31T16:00:00+08:00",
    )
    second = agent.repo.create_long_term_decision(
        content="每天读书半小时",
        created_at="2026-05-31T16:30:00+08:00",
        next_review_at="",
        review_interval_days=0,
        status="pending_review_time",
    )

    result = agent.record_life_event(
        "6月15日晚上8点复盘",
        received_at="2026-05-31T17:53:46+08:00",
    )

    updated_first = agent.repo.get_long_term_decision(first["decision"]["id"])
    updated_second = agent.repo.get_long_term_decision(second["id"])
    assert result["status"] == "decision_recorded"
    assert updated_first["status"] == "active"
    assert updated_first["next_review_at"] == "2026-06-15T20:00:00+08:00"
    assert updated_second["status"] == "pending_review_time"
    assert updated_second["next_review_at"] == ""


def test_follow_up_review_time_merges_duplicate_active_decision(agent: LifeAgent) -> None:
    active = agent.repo.create_long_term_decision(
        content="未来三个月优先打磨 Life Assistant",
        created_at="2026-05-31T15:00:00+08:00",
        next_review_at="2026-06-10T20:00:00+08:00",
        review_interval_days=10,
    )
    pending = agent.repo.create_long_term_decision(
        content="未来三个月优先打磨 Life Assistant",
        created_at="2026-05-31T16:00:00+08:00",
        next_review_at="",
        review_interval_days=0,
        status="pending_review_time",
    )

    result = agent.record_life_event(
        "6月15日晚上8点复盘",
        received_at="2026-05-31T17:53:46+08:00",
    )

    updated_active = agent.repo.get_long_term_decision(active["id"])
    superseded = agent.repo.get_long_term_decision(pending["id"])
    assert result["status"] == "decision_recorded"
    assert updated_active["next_review_at"] == "2026-06-15T20:00:00+08:00"
    assert superseded["status"] == "superseded"


def test_life_tick_sends_long_term_decision_review(agent: LifeAgent) -> None:
    decision = agent.repo.create_long_term_decision(
        content="未来三个月优先打磨 Life Assistant",
        created_at="2026-05-31T16:00:00+08:00",
        next_review_at="2026-06-30T16:00:00+08:00",
        review_interval_days=30,
    )

    result = agent.life_tick(
        now="2026-06-30T16:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    assert result["selected"]["reminder_type"] == "long_term_decision_review"
    assert decision["id"] in result["selected"]["dedupe_key"]
    assert "未来三个月优先打磨 Life Assistant" in result["chat_preview"]
    assert "是否还成立" in result["chat_preview"]


def test_long_term_review_reply_uses_reminder_context_not_unrelated_active_decision(agent: LifeAgent) -> None:
    due = agent.repo.create_long_term_decision(
        content="未来三个月优先打磨 Life Assistant",
        created_at="2026-05-31T16:00:00+08:00",
        next_review_at="2026-06-30T16:00:00+08:00",
        review_interval_days=30,
    )
    unrelated = agent.repo.create_long_term_decision(
        content="每天读书半小时",
        created_at="2026-06-30T16:01:00+08:00",
        next_review_at="2026-07-30T16:00:00+08:00",
        review_interval_days=30,
    )
    agent.life_tick(
        now="2026-06-30T16:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    result = agent.record_life_event(
        "继续有效，8月1号上午9点再复盘",
        received_at="2026-06-30T16:05:00+08:00",
    )

    updated_due = agent.repo.get_long_term_decision(due["id"])
    updated_unrelated = agent.repo.get_long_term_decision(unrelated["id"])
    assert result["status"] == "decision_review_recorded"
    assert updated_due["next_review_at"] == "2026-08-01T09:00:00+08:00"
    assert updated_unrelated["next_review_at"] == "2026-07-30T16:00:00+08:00"


def test_long_term_decision_review_reply_without_next_time_asks_follow_up(agent: LifeAgent) -> None:
    decision = agent.repo.create_long_term_decision(
        content="未来三个月优先打磨 Life Assistant",
        created_at="2026-05-31T16:00:00+08:00",
        next_review_at="2026-06-30T16:00:00+08:00",
        review_interval_days=0,
    )

    result = agent.record_life_event(
        "调整为：未来三个月优先跑通 Life Assistant 的真实使用",
        received_at="2026-06-30T16:05:00+08:00",
    )

    updated = agent.repo.get_long_term_decision(decision["id"])
    reviews = agent.repo.conn.execute("SELECT * FROM life_decision_reviews").fetchall()
    assert result["status"] == "decision_needs_review_time"
    assert updated["content"] == "未来三个月优先跑通 Life Assistant 的真实使用"
    assert updated["status"] == "pending_review_time"
    assert updated["next_review_at"] == ""
    assert len(reviews) == 1
    assert reviews[0]["review_note"] == "调整为：未来三个月优先跑通 Life Assistant 的真实使用"
    assert "下一次复盘" in result["chat_preview"]


class LongTermDecisionReviewWithTimeParser:
    def parse(self, message, *, received_at):
        return LifeIntent(
            intent="review_long_term_decision",
            payload={
                "review_note": "调整为：未来三个月优先跑通 Life Assistant 的真实使用",
                "new_content": "未来三个月优先跑通 Life Assistant 的真实使用",
                "next_review_at": "2026-08-01T09:00:00+08:00",
            },
        )


def test_long_term_decision_review_reply_uses_user_specified_next_time(tmp_path) -> None:
    conn = init_life_db(str(tmp_path / "life.db"))
    agent = LifeAgent(LifeRepo(conn), parser=LongTermDecisionReviewWithTimeParser())
    decision = agent.repo.create_long_term_decision(
        content="未来三个月优先打磨 Life Assistant",
        created_at="2026-05-31T16:00:00+08:00",
        next_review_at="2026-06-30T16:00:00+08:00",
        review_interval_days=0,
    )

    result = agent.record_life_event(
        "调整为：未来三个月优先跑通 Life Assistant 的真实使用，8月1号上午9点再复盘",
        received_at="2026-06-30T16:05:00+08:00",
    )

    updated = agent.repo.get_long_term_decision(decision["id"])
    reviews = agent.repo.conn.execute("SELECT * FROM life_decision_reviews").fetchall()
    assert result["status"] == "decision_review_recorded"
    assert updated["content"] == "未来三个月优先跑通 Life Assistant 的真实使用"
    assert updated["status"] == "active"
    assert updated["next_review_at"] == "2026-08-01T09:00:00+08:00"
    assert len(reviews) == 1
    assert reviews[0]["next_review_at"] == "2026-08-01T09:00:00+08:00"


def test_close_long_term_decision_reply(agent: LifeAgent) -> None:
    decision = agent.repo.create_long_term_decision(
        content="未来三个月优先打磨 Life Assistant",
        created_at="2026-05-31T16:00:00+08:00",
        next_review_at="2026-06-30T16:00:00+08:00",
        review_interval_days=30,
    )

    result = agent.record_life_event(
        "关闭这个决定",
        received_at="2026-06-30T16:05:00+08:00",
    )

    updated = agent.repo.get_long_term_decision(decision["id"])
    assert result["status"] == "decision_closed"
    assert updated["status"] == "closed"


def test_life_tick_splits_cross_midnight_sitting_window(agent: LifeAgent) -> None:
    window = agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-30T23:30:00+08:00",
        source="mac_activity",
        status="active",
    )

    result = agent.life_tick(
        now="2026-05-31T00:20:00+08:00",
        activity=ActivitySnapshot(idle_seconds=30, available=True),
        dry_run=True,
    )

    closed = agent.repo.get_event(window["id"])
    active = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'sedentary_start' AND status = 'active'"
    ).fetchone()
    assert (result["selected"] or {}).get("reminder_type") != "sedentary"
    assert closed["status"] == "day_split"
    assert closed["ended_at"] == "2026-05-31T00:00:00+08:00"
    assert closed["duration_minutes"] == 30
    assert active is not None
    assert active["occurred_at"] == "2026-05-31T00:00:00+08:00"


def test_life_tick_defers_reminder_during_fresh_knowledge_choice(agent: LifeAgent) -> None:
    """刚产生 Theme/Garden 待选择项时，不应插入久坐提醒打断多轮知识流程。"""
    agent.config = LifeConfig(notifications_enabled=True)
    agent.repo.conn.execute(
        """INSERT INTO pending_questions
           (id, agent_type, question_text, context_ref, status, related_capture_ids,
            created_at, expires_at)
           VALUES (?, 'theme', '要展开哪一组？', 'theme-agent', 'pending', '[]', ?, ?)""",
        (
            "q1",
            "2026-05-31T15:09:59+08:00",
            "2026-05-31T23:59:00+08:00",
        ),
    )
    agent.repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-31T13:50:00+08:00",
        source="mac_activity",
        status="active",
    )

    result = agent.life_tick(
        now="2026-05-31T15:10:00+08:00",
        activity=ActivitySnapshot(idle_seconds=0, available=True),
        dry_run=False,
    )

    assert result["status"] == "idle"
    assert result["selected"] is None
    assert result["meta"]["should_notify"] is False
    assert "knowledge_workflow_active" in result["warnings"]


def test_life_tick_writes_compact_log_line(tmp_path) -> None:
    conn = init_life_db(str(tmp_path / "life.db"))
    log_path = tmp_path / "life.log"
    agent = LifeAgent(
        LifeRepo(conn),
        config=LifeConfig(log_path=str(log_path)),
    )

    agent.life_tick(
        now="2026-05-29T10:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    text = log_path.read_text(encoding="utf-8")
    assert '"tick_time": "2026-05-29T10:00:00+08:00"' in text
    assert '"selected_reminder": "water"' in text
    assert '"mac_activity_unavailable"' in text


def test_life_tick_marks_sending_only_when_notification_will_send(tmp_path) -> None:
    conn = init_life_db(str(tmp_path / "life.db"))
    agent = LifeAgent(LifeRepo(conn), config=LifeConfig(notifications_enabled=False))

    result = agent.life_tick(
        now="2026-05-29T10:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=False,
    )

    row = conn.execute(
        "SELECT status FROM life_reminder_logs WHERE reminder_type = 'exercise'"
    ).fetchone()
    assert result["meta"]["should_notify"] is False
    assert row["status"] == "scheduled"
