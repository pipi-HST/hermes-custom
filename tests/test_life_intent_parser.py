"""Tests for Life Assistant natural-language intent parsing."""

from __future__ import annotations

from datetime import datetime

import pytest

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
    assert intent.occurred_at == ""
    assert intent.payload["meal_slot"] == "lunch"
    assert intent.payload["content"] == "腰果饼干"
    assert intent.payload["meal_time_precision"] == "missing"


def test_parser_extracts_explicit_meal_time() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "12点半午饭吃了腰果饼干",
        received_at=datetime.fromisoformat("2026-05-30T16:20:00+08:00"),
    )

    assert intent.intent == "record_event"
    assert intent.event_type == "meal"
    assert intent.occurred_at == "2026-05-30T12:30:00+08:00"
    assert intent.payload["meal_slot"] == "lunch"
    assert intent.payload["content"] == "腰果饼干"
    assert intent.payload["meal_time_precision"] == "exact"


def test_parser_removes_trailing_eating_time_phrase_from_meal_content() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "中午吃了蘑菇肉臊蛋包饭，十二点半吃的",
        received_at=datetime.fromisoformat("2026-06-01T21:32:35+08:00"),
    )

    assert intent.intent == "record_event"
    assert intent.event_type == "meal"
    assert intent.occurred_at == "2026-06-01T12:30:00+08:00"
    assert intent.payload["meal_slot"] == "lunch"
    assert intent.payload["content"] == "蘑菇肉臊蛋包饭"
    assert intent.payload["meal_time_precision"] == "exact"


@pytest.mark.parametrize(
    ("message", "expected_time", "expected_slot", "expected_content"),
    [
        (
            "晚饭吃了馄饨和烤肠和桂花糕，大概七点吃的",
            "2026-06-01T19:00:00+08:00",
            "dinner",
            "馄饨和烤肠和桂花糕",
        ),
        (
            "七点晚饭吃了馄饨和烤肠",
            "2026-06-01T19:00:00+08:00",
            "dinner",
            "馄饨和烤肠",
        ),
        (
            "午饭吃了面包，大概一点吃的",
            "2026-06-01T13:00:00+08:00",
            "lunch",
            "面包",
        ),
        (
            "一点午饭吃了面包",
            "2026-06-01T13:00:00+08:00",
            "lunch",
            "面包",
        ),
    ],
)
def test_parser_uses_meal_context_for_ambiguous_inline_meal_times(
    message: str,
    expected_time: str,
    expected_slot: str,
    expected_content: str,
) -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        message,
        received_at=datetime.fromisoformat("2026-06-01T21:00:00+08:00"),
    )

    assert intent.intent == "record_event"
    assert intent.event_type == "meal"
    assert intent.occurred_at == expected_time
    assert intent.payload["meal_slot"] == expected_slot
    assert intent.payload["content"] == expected_content
    assert intent.payload["meal_time_precision"] == "exact"


def test_parser_treats_meal_slot_time_phrase_as_pending_time_update() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "午餐时间是下午两点",
        received_at=datetime.fromisoformat("2026-05-31T15:13:29+08:00"),
    )

    assert intent.intent == "modify_latest"
    assert intent.event_type == ""
    assert intent.payload["meal_occurred_at"] == "2026-05-31T14:00:00+08:00"
    assert intent.payload["meal_slot"] == "lunch"


def test_parser_treats_named_drink_with_meal_context_as_meal() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "中午还喝了茉莉乌龙茶",
        received_at=datetime.fromisoformat("2026-05-31T14:05:00+08:00"),
    )

    assert intent.intent == "record_event"
    assert intent.event_type == "meal"
    assert intent.occurred_at == ""
    assert intent.payload["meal_slot"] == "lunch"
    assert intent.payload["content"] == "茉莉乌龙茶"
    assert intent.payload["meal_item_kind"] == "beverage"
    assert intent.payload["meal_time_precision"] == "missing"


def test_parser_keeps_explicit_water_as_water() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "喝了一杯水",
        received_at=datetime.fromisoformat("2026-05-31T14:05:00+08:00"),
    )

    assert intent.intent == "record_event"
    assert intent.event_type == "water"


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


def test_parser_treats_tonight_exercise_time_as_evening() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "今晚八点再运动",
        received_at=datetime.fromisoformat("2026-05-31T14:54:40+08:00"),
    )

    assert intent.intent == "reschedule_exercise"
    assert intent.scheduled_for == "2026-05-31T20:00:00+08:00"


def test_parser_extracts_explicit_long_term_decision() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "长期决定：未来三个月优先打磨 Life Assistant",
        received_at=datetime.fromisoformat("2026-05-31T16:00:00+08:00"),
    )

    assert intent.intent == "record_long_term_decision"
    assert intent.payload["content"] == "未来三个月优先打磨 Life Assistant"
    assert "review_interval_days" not in intent.payload
    assert "next_review_at" not in intent.payload


def test_parser_lets_llm_supply_long_term_decision_review_time() -> None:
    def fallback(text, received_at):
        assert text == "长期决定：未来三个月优先打磨 Life Assistant，下个月15号晚上8点复盘"
        return LifeIntent(
            intent="record_long_term_decision",
            payload={
                "content": "未来三个月优先打磨 Life Assistant",
                "next_review_at": "2026-06-15T20:00:00+08:00",
            },
        )

    parser = LifeIntentParser(llm_fallback=fallback)

    intent = parser.parse(
        "长期决定：未来三个月优先打磨 Life Assistant，下个月15号晚上8点复盘",
        received_at=datetime.fromisoformat("2026-05-31T16:00:00+08:00"),
    )

    assert intent.intent == "record_long_term_decision"
    assert intent.payload["content"] == "未来三个月优先打磨 Life Assistant"
    assert intent.payload["next_review_at"] == "2026-06-15T20:00:00+08:00"


def test_parser_extracts_long_term_decision_follow_up_review_time() -> None:
    parser = LifeIntentParser()

    intent = parser.parse(
        "6月15日晚上8点复盘",
        received_at=datetime.fromisoformat("2026-05-31T17:53:46+08:00"),
    )

    assert intent.intent == "set_long_term_decision_review_time"
    assert intent.payload["next_review_at"] == "2026-06-15T20:00:00+08:00"


def test_parser_extracts_long_term_decision_review_reply() -> None:
    parser = LifeIntentParser()

    keep = parser.parse(
        "继续有效",
        received_at=datetime.fromisoformat("2026-06-30T21:00:00+08:00"),
    )
    adjust = parser.parse(
        "调整为：未来三个月优先跑通 Life Assistant 的真实使用",
        received_at=datetime.fromisoformat("2026-06-30T21:01:00+08:00"),
    )
    close = parser.parse(
        "关闭这个决定",
        received_at=datetime.fromisoformat("2026-06-30T21:02:00+08:00"),
    )

    assert keep.intent == "review_long_term_decision"
    assert keep.payload["review_note"] == "继续有效"
    assert adjust.intent == "review_long_term_decision"
    assert adjust.payload["new_content"] == "未来三个月优先跑通 Life Assistant 的真实使用"
    assert close.intent == "close_long_term_decision"


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


def test_parser_can_prefer_llm_for_ambiguous_life_records() -> None:
    calls = []

    def fallback(text, received_at):
        calls.append(text)
        return LifeIntent(
            intent="record_event",
            event_type="meal",
            occurred_at="",
            payload={
                "meal_slot": "lunch",
                "content": "茉莉乌龙茶",
                "meal_item_kind": "beverage",
                "meal_time_precision": "missing",
            },
        )

    parser = LifeIntentParser(
        llm_fallback=fallback,
        prefer_llm_for_life_records=True,
    )

    intent = parser.parse(
        "中午还喝了茉莉乌龙茶",
        received_at=datetime.fromisoformat("2026-05-31T14:05:00+08:00"),
    )

    assert calls == ["中午还喝了茉莉乌龙茶"]
    assert intent.intent == "record_event"
    assert intent.event_type == "meal"
    assert intent.payload["content"] == "茉莉乌龙茶"


def test_parser_keeps_high_confidence_rules_ahead_of_llm_preference() -> None:
    calls = []

    def fallback(text, received_at):
        calls.append(text)
        return LifeIntent(intent="unknown")

    parser = LifeIntentParser(
        llm_fallback=fallback,
        prefer_llm_for_life_records=True,
    )

    intent = parser.parse(
        "喝水了",
        received_at=datetime.fromisoformat("2026-05-31T14:05:00+08:00"),
    )

    assert calls == []
    assert intent.intent == "record_event"
    assert intent.event_type == "water"


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
