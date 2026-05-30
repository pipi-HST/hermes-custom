"""Tests for Life Assistant natural-language intent parsing."""

from __future__ import annotations

from datetime import datetime

from agents.life_intent_parser import LifeIntent, LifeIntentLLMFallback, LifeIntentParser


def test_parser_extracts_explicit_wake_time() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "中午13点半醒的",
        received_at=datetime.fromisoformat("2026-05-30T13:40:00+08:00"),
    )

    assert intent.intent == "record_event"
    assert intent.event_type == "sleep_end"
    assert intent.occurred_at == "2026-05-30T13:30:00+08:00"


def test_parser_extracts_sleep_session_with_start_and_end() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "昨晚1点睡，今天8点醒",
        received_at=datetime.fromisoformat("2026-05-30T09:00:00+08:00"),
    )

    assert intent.intent == "record_event"
    assert intent.event_type == "sleep_session"
    assert intent.occurred_at == "2026-05-30T01:00:00+08:00"
    assert intent.payload["ended_at"] == "2026-05-30T08:00:00+08:00"


def test_parser_extracts_meal_without_keyword_enum() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "午饭吃了腰果饼干",
        received_at=datetime.fromisoformat("2026-05-30T12:20:00+08:00"),
    )

    assert intent.intent == "record_event"
    assert intent.event_type == "meal"
    assert intent.payload["meal_slot"] == "lunch"
    assert intent.payload["content"] == "腰果饼干"


def test_parser_keeps_manual_status_query_out_of_records() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "我今天坐了多久？",
        received_at=datetime.fromisoformat("2026-05-30T18:00:00+08:00"),
    )

    assert intent.intent == "manual_status_query"
    assert intent.event_type == ""


def test_parser_classifies_health_consultation_without_recording() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "健康：我最近睡不好，是不是身体有问题？",
        received_at=datetime.fromisoformat("2026-05-30T18:00:00+08:00"),
    )

    assert intent.intent == "health_consultation"
    assert intent.event_type == ""


def test_parser_extracts_sedentary_pause_duration() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "暂停久坐警报 2 小时",
        received_at=datetime.fromisoformat("2026-05-30T18:00:00+08:00"),
    )

    assert intent.intent == "pause_sedentary"
    assert intent.scheduled_for == "2026-05-30T20:00:00+08:00"
    assert intent.duration_minutes == 120


def test_parser_classifies_sedentary_activity_reply() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "已活动",
        received_at=datetime.fromisoformat("2026-05-30T10:05:00+08:00"),
    )

    assert intent.intent == "sedentary_activity_reply"


def test_parser_extracts_latest_record_modification() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "上一条改成加餐",
        received_at=datetime.fromisoformat("2026-05-30T12:20:00+08:00"),
    )

    assert intent.intent == "modify_latest"
    assert intent.payload["change_text"] == "加餐"


def test_parser_extracts_planned_exercise_time_and_type() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "我打算下午6点练八段锦。",
        received_at=datetime.fromisoformat("2026-05-30T17:37:49+08:00"),
    )

    assert intent.intent == "reschedule_exercise"
    assert intent.scheduled_for == "2026-05-30T18:00:00+08:00"
    assert intent.payload["exercise_type"] == "八段锦"


def test_parser_extracts_exercise_time_without_plan_keyword() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "我晚上八点练",
        received_at=datetime.fromisoformat("2026-05-30T19:51:18+08:00"),
    )

    assert intent.intent == "reschedule_exercise"
    assert intent.scheduled_for == "2026-05-30T20:00:00+08:00"


def test_parser_uses_fallback_only_when_rules_do_not_match() -> None:
    calls = []

    def fallback(text, received_at):
        calls.append((text, received_at))
        return LifeIntent(
            intent="record_event",
            event_type="meal",
            occurred_at=received_at.isoformat(),
            payload={"meal_slot": "snack", "content": "一小把坚果"},
        )

    parser = LifeIntentParser(llm_fallback=fallback)
    intent = parser.parse(
        "刚补了一小把坚果",
        received_at=datetime.fromisoformat("2026-05-30T15:10:00+08:00"),
    )
    deterministic = parser.parse(
        "喝水了",
        received_at=datetime.fromisoformat("2026-05-30T15:20:00+08:00"),
    )

    assert intent.intent == "record_event"
    assert intent.event_type == "meal"
    assert intent.payload["content"] == "一小把坚果"
    assert deterministic.event_type == "water"
    assert len(calls) == 1


def test_llm_fallback_converts_json_response_to_intent() -> None:
    requests = []

    def post_json(url, *, headers, json, timeout):
        requests.append((url, headers, json, timeout))
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"intent":"record_event","event_type":"meal",'
                            '"occurred_at":"2026-05-30T15:10:00+08:00",'
                            '"payload":{"meal_slot":"snack","content":"一小把坚果"}}'
                        )
                    }
                }
            ]
        }

    fallback = LifeIntentLLMFallback(
        api_key="sk-test",
        model="test-model",
        base_url="https://example.com",
        post_json=post_json,
    )

    intent = fallback(
        "刚补了一小把坚果",
        datetime.fromisoformat("2026-05-30T15:10:00+08:00"),
    )

    assert intent is not None
    assert intent.intent == "record_event"
    assert intent.event_type == "meal"
    assert intent.payload["content"] == "一小把坚果"
    assert requests[0][0] == "https://example.com/chat/completions"
