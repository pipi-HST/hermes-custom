"""Tests for Life Assistant memory analysis."""

from __future__ import annotations

from agents.life_agent import ActivitySnapshot, LifeAgent
from agents.life_memory import LifeMemoryAnalyzer
from db.life_repo import LifeRepo
from db.life_schema import init_life_db


def _repo(tmp_path) -> LifeRepo:
    conn = init_life_db(str(tmp_path / "life.db"))
    return LifeRepo(conn)


def test_memory_analyzer_forms_weak_sleep_memory_from_repeated_records(tmp_path) -> None:
    repo = _repo(tmp_path)
    for day, minutes in [
        ("2026-05-27", 420),
        ("2026-05-28", 450),
        ("2026-05-29", 390),
    ]:
        repo.create_event(
            event_type="sleep_end",
            occurred_at=f"{day}T08:00:00+08:00",
            duration_minutes=minutes,
            source="wechat",
            payload={"wake_date": day},
        )

    result = LifeMemoryAnalyzer(repo).refresh(now="2026-06-01T10:00:00+08:00")

    memories = repo.list_memory_items(domains=["life.sleep"])
    observations = repo.list_memory_observations(domain="life.sleep")
    assert result["created_or_updated_items"] == 1
    assert len(memories) == 1
    assert memories[0]["status"] == "weak"
    assert memories[0]["memory_type"] == "pattern"
    assert "平均睡眠7小时" in memories[0]["content"]
    assert observations[0]["observation_type"] == "sleep_pattern"


def test_memory_analyzer_does_not_form_pattern_from_single_record(tmp_path) -> None:
    repo = _repo(tmp_path)
    repo.create_event(
        event_type="sleep_end",
        occurred_at="2026-05-29T08:00:00+08:00",
        duration_minutes=420,
        source="wechat",
        payload={"wake_date": "2026-05-29"},
    )

    LifeMemoryAnalyzer(repo).refresh(now="2026-06-01T10:00:00+08:00")

    assert repo.list_memory_items(domains=["life.sleep"]) == []


def test_memory_analyzer_does_not_duplicate_same_day_observations(tmp_path) -> None:
    repo = _repo(tmp_path)
    for day in ["2026-05-27", "2026-05-28", "2026-05-29"]:
        repo.create_event(
            event_type="sleep_end",
            occurred_at=f"{day}T08:00:00+08:00",
            duration_minutes=420,
            source="wechat",
            payload={"wake_date": day},
        )

    analyzer = LifeMemoryAnalyzer(repo)
    first = analyzer.refresh(now="2026-06-01T10:00:00+08:00")
    second = analyzer.refresh(now="2026-06-01T10:01:00+08:00")

    assert first["observations"] == 1
    assert second["observations"] == 0
    assert len(repo.list_memory_observations(domain="life.sleep")) == 1


def test_life_tick_refreshes_memories_and_records_hits_for_selected_reminder(tmp_path) -> None:
    repo = _repo(tmp_path)
    agent = LifeAgent(repo)
    repo.create_event(
        event_type="exercise",
        occurred_at="2026-06-01T08:00:00+08:00",
        source="wechat",
        status="completed_unknown",
    )
    memory = repo.upsert_memory_item(
        domain="life.water",
        memory_type="pattern",
        status="weak",
        content="最近14天有3天记录喝水，平均每天约1次。",
        confidence="medium",
        source="event_stats",
        effective_from="2026-06-01T09:00:00+08:00",
        evidence={"water_days": 3},
    )

    result = agent.life_tick(
        now="2026-06-01T10:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    log = repo.conn.execute(
        "SELECT * FROM life_reminder_logs WHERE reminder_type = 'water'"
    ).fetchone()
    hits = repo.list_memory_hits(limit=5)
    assert result["selected"]["reminder_type"] == "water"
    assert memory["id"] in log["payload_json"]
    assert hits[0]["memory_id"] == memory["id"]
    assert hits[0]["used_by"] == "life_tick"
