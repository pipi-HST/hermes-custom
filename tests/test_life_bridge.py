"""Tests for Life Assistant Hermes bridge functions."""

from __future__ import annotations

from bridge.result_envelope import require_envelope
from bridge import life_bridge


def test_record_life_event_returns_envelope(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(life_bridge, "_agent", None)
    monkeypatch.setattr(life_bridge, "_db_path", str(tmp_path / "life.db"))

    envelope = life_bridge.record_life_event(
        "午饭吃了牛肉饭",
        received_at="2026-05-29T12:10:00+08:00",
    )

    checked = require_envelope(envelope)
    assert checked["agent"] == "life"
    assert checked["intent"] == "record_life_event"
    assert checked["status"] == "success"
    assert checked["meta"]["event_type"] == "meal"
    assert checked["meta"]["should_notify"] is False


def test_life_tick_returns_envelope_with_should_notify_false_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(life_bridge, "_agent", None)
    monkeypatch.setattr(life_bridge, "_db_path", str(tmp_path / "life.db"))

    envelope = life_bridge.life_tick(
        now="2026-05-29T10:30:00+08:00",
        dry_run=True,
    )

    checked = require_envelope(envelope)
    assert checked["agent"] == "life"
    assert checked["intent"] == "life_tick"
    assert checked["meta"]["should_notify"] is False


def test_life_tick_can_notify_when_config_enabled(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "database:",
                f"  path: {tmp_path / 'life.db'}",
                "life:",
                "  notifications_enabled: true",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(life_bridge, "_agent", None)
    monkeypatch.setattr(life_bridge, "_db_path", None)
    monkeypatch.setattr(life_bridge, "_config_path", config_path)
    monkeypatch.setattr(
        life_bridge,
        "_read_mac_activity",
        lambda: life_bridge.ActivitySnapshot(idle_seconds=None, available=False),
    )

    envelope = life_bridge.life_tick(
        now="2026-05-29T10:30:00+08:00",
        dry_run=False,
    )

    checked = require_envelope(envelope)
    assert checked["meta"]["should_notify"] is True
    assert checked["meta"]["selected_reminder_type"] == "action_digest"


def test_recent_mac_sleep_interruption_requires_six_minutes() -> None:
    short_log = "\n".join(
        [
            "2026-06-01 10:31:09 +0800 Sleep                 Entering Sleep state due to 'Idle Sleep'",
            "2026-06-01 10:35:43 +0800 Wake                  Wake from Deep Idle",
        ]
    )
    long_log = "\n".join(
        [
            "2026-06-01 10:31:09 +0800 Sleep                 Entering Sleep state due to 'Idle Sleep'",
            "2026-06-01 10:38:43 +0800 Wake                  Wake from Deep Idle",
        ]
    )
    now = life_bridge._parse_pmset_time("2026-06-01 10:40:00 +0800")

    assert life_bridge._recent_mac_interruption(short_log, now=now) is None
    assert life_bridge._recent_mac_interruption(long_log, now=now) == (
        "sleep",
        454,
        "2026-06-01T10:31:09+08:00",
        "2026-06-01T10:38:43+08:00",
    )


def test_read_mac_activity_includes_recent_sleep_interruption(monkeypatch) -> None:
    def fake_check_output(command, **kwargs):
        if command[:3] == ["ioreg", "-c", "IOHIDSystem"]:
            return '    | |   "HIDIdleTime" = 1000000000\n'
        if command[:3] == ["pmset", "-g", "log"]:
            return "\n".join(
                [
                    "2026-06-01 10:31:09 +0800 Sleep                 Entering Sleep state due to 'Idle Sleep'",
                    "2026-06-01 10:45:43 +0800 Wake                  Wake from Deep Idle",
                ]
            )
        raise AssertionError(command)

    monkeypatch.setattr(life_bridge.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(
        life_bridge,
        "_now",
        lambda: life_bridge._parse_pmset_time("2026-06-01 10:47:00 +0800"),
    )

    activity = life_bridge._read_mac_activity()

    assert activity.idle_seconds == 1
    assert activity.sleeping is True
    assert activity.interruption_seconds == 874
    assert activity.interruption_source == "sleep"
    assert activity.interruption_started_at == "2026-06-01T10:31:09+08:00"
    assert activity.interruption_ended_at == "2026-06-01T10:45:43+08:00"


def test_reconcile_life_delivery_returns_envelope(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(life_bridge, "_agent", None)
    monkeypatch.setattr(life_bridge, "_db_path", str(tmp_path / "life.db"))
    agent = life_bridge._init()
    agent.repo.create_reminder_log(
        reminder_type="water",
        scheduled_for="2026-05-30T10:00:00+08:00",
        status="sending",
        dedupe_key="water:2026-05-30T10:00",
    )

    envelope = life_bridge.reconcile_life_delivery(
        completed_at="2026-05-30T10:00:30+08:00",
        delivery_error="",
    )

    checked = require_envelope(envelope)
    assert checked["intent"] == "reconcile_life_delivery"
    assert checked["meta"]["should_notify"] is False
    assert checked["meta"]["updated"] == 1
    assert checked["meta"]["delivery_status"] == "sent"


def test_life_memory_review_and_forget_return_envelopes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(life_bridge, "_agent", None)
    monkeypatch.setattr(life_bridge, "_db_path", str(tmp_path / "life.db"))
    agent = life_bridge._init()
    agent.repo.upsert_memory_item(
        domain="life.copy",
        memory_type="copy_preference",
        status="active",
        content="用户喜欢直接、克制的提醒。",
        confidence="medium",
        source="user_statement",
        effective_from="2026-06-01T10:00:00+08:00",
        evidence={"text": "别太鸡汤"},
    )

    review = require_envelope(life_bridge.life_memory_review())
    forget = require_envelope(life_bridge.life_memory_forget("克制"))

    assert review["intent"] == "life_memory_review"
    assert "直接、克制" in review["chat_preview"]
    assert forget["intent"] == "life_memory_forget"
    assert forget["meta"]["updated"] == 1


def test_init_configures_life_intent_llm_fallback(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "database:",
                f"  path: {tmp_path / 'life.db'}",
                "llm:",
                "  api_key: sk-test",
                "  model: deepseek-v4-pro",
                "  base_url: https://example.com",
            ]
        ),
        encoding="utf-8",
    )
    created = []

    class FakeFallback:
        def __init__(self, **kwargs):
            created.append(kwargs)

        def __call__(self, text, received_at):
            return None

    monkeypatch.setattr(life_bridge, "_agent", None)
    monkeypatch.setattr(life_bridge, "_db_path", None)
    monkeypatch.setattr(life_bridge, "_config_path", config_path)
    monkeypatch.setattr(life_bridge, "LifeIntentLLMFallback", FakeFallback)

    agent = life_bridge._init()

    assert agent.parser.llm_fallback is not None
    assert agent.parser.prefer_llm_for_life_records is True
    assert created == [
        {
            "api_key": "sk-test",
            "model": "deepseek-v4-flash",
            "base_url": "https://example.com",
        }
    ]


def test_init_allows_disabling_flexible_life_record_parser(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "database:",
                f"  path: {tmp_path / 'life.db'}",
                "llm:",
                "  api_key: sk-test",
                "life:",
                "  prefer_llm_for_life_records: false",
            ]
        ),
        encoding="utf-8",
    )

    class FakeFallback:
        def __init__(self, **kwargs):
            pass

        def __call__(self, text, received_at):
            return None

    monkeypatch.setattr(life_bridge, "_agent", None)
    monkeypatch.setattr(life_bridge, "_db_path", None)
    monkeypatch.setattr(life_bridge, "_config_path", config_path)
    monkeypatch.setattr(life_bridge, "LifeIntentLLMFallback", FakeFallback)

    agent = life_bridge._init()

    assert agent.parser.llm_fallback is not None
    assert agent.parser.prefer_llm_for_life_records is False
