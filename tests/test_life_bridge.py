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
    assert checked["meta"]["selected_reminder_type"] == "exercise"


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
    assert created == [
        {
            "api_key": "sk-test",
            "model": "deepseek-v4-flash",
            "base_url": "https://example.com",
        }
    ]
