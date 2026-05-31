"""Tests for Life Assistant schema and repository."""

from __future__ import annotations

import sqlite3

import pytest

from db.life_repo import LifeRepo
from db.life_schema import init_life_db


@pytest.fixture
def life_repo(tmp_path):
    conn = init_life_db(str(tmp_path / "life.db"))
    return LifeRepo(conn)


def test_life_schema_creates_isolated_life_tables(tmp_path) -> None:
    conn = init_life_db(str(tmp_path / "life.db"))

    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }

    assert {
        "life_events",
        "life_reminder_rules",
        "life_reminder_logs",
        "life_locks",
        "life_daily_reviews",
        "life_weekly_reviews",
    }.issubset(tables)
    assert "captures" in tables


def test_life_schema_migrates_existing_connection(tmp_path) -> None:
    db_path = tmp_path / "life.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE unrelated (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    migrated = init_life_db(str(db_path))

    assert migrated.execute("SELECT name FROM sqlite_master WHERE name='unrelated'").fetchone()
    assert migrated.execute("SELECT name FROM sqlite_master WHERE name='life_events'").fetchone()


def test_life_schema_adds_weekly_report_columns_to_existing_table(tmp_path) -> None:
    db_path = tmp_path / "life.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE life_weekly_reviews (
            id TEXT PRIMARY KEY,
            week_start TEXT NOT NULL,
            week_end TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            labels_json TEXT NOT NULL DEFAULT '{}',
            patterns_json TEXT NOT NULL DEFAULT '{}',
            recommendations_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(week_start, week_end)
        )"""
    )
    conn.commit()

    migrated = init_life_db(str(db_path))
    columns = {
        row["name"]
        for row in migrated.execute("PRAGMA table_info(life_weekly_reviews)").fetchall()
    }

    assert {"headline", "report_text", "evidence_json", "delivery_json"}.issubset(columns)


def test_life_repo_enriches_existing_weekly_review_missing_report_fields(life_repo: LifeRepo) -> None:
    life_repo.conn.execute(
        """INSERT INTO life_weekly_reviews
           (id, week_start, week_end, summary, headline, report_text,
            labels_json, patterns_json, recommendations_json, evidence_json, delivery_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "old-week",
            "2026-05-25",
            "2026-05-31",
            "旧版周复盘",
            "",
            "",
            "{}",
            "{}",
            "[]",
            "[]",
            "{}",
        ),
    )
    life_repo.conn.commit()

    review = life_repo.create_weekly_review(
        week_start="2026-05-25",
        week_end="2026-05-31",
        summary="新版周复盘",
        labels={"sleep": "有记录"},
        patterns={"sleep_days": 2},
        recommendations=["减少连续久坐"],
        headline="久坐偏高，优先中断坐姿",
        report_text="总体：久坐偏高。建议：每小时起身一次。",
        evidence=[{"type": "sedentary", "text": "05-31 久坐90分钟"}],
        delivery={"wechat": "pending", "feishu": "pending"},
    )

    assert review["id"] == "old-week"
    assert review["summary"] == "旧版周复盘"
    assert review["headline"] == "久坐偏高，优先中断坐姿"
    assert review["report_text"] == "总体：久坐偏高。建议：每小时起身一次。"
    assert review["evidence_json"] == [{"type": "sedentary", "text": "05-31 久坐90分钟"}]
    assert review["delivery_json"] == {"wechat": "pending", "feishu": "pending"}


def test_life_repo_records_and_fetches_events(life_repo: LifeRepo) -> None:
    event = life_repo.create_event(
        event_type="meal",
        occurred_at="2026-05-29T12:10:00+08:00",
        source="wechat",
        raw_text="午饭吃了牛肉饭",
        payload={"meal_slot": "lunch", "content": "牛肉饭"},
    )

    fetched = life_repo.get_event(event["id"])

    assert fetched is not None
    assert fetched["event_type"] == "meal"
    assert fetched["payload_json"] == {"meal_slot": "lunch", "content": "牛肉饭"}


def test_life_repo_lists_latest_user_event_within_window(life_repo: LifeRepo) -> None:
    old = life_repo.create_event(
        event_type="water",
        occurred_at="2026-05-29T10:00:00+08:00",
        source="wechat",
        raw_text="喝水了",
    )
    latest = life_repo.create_event(
        event_type="meal",
        occurred_at="2026-05-29T12:00:00+08:00",
        source="wechat",
        raw_text="午饭吃了牛肉饭",
    )

    found = life_repo.latest_user_event(
        now="2026-05-29T12:20:00+08:00",
        window_minutes=30,
    )

    assert found is not None
    assert found["id"] == latest["id"]
    assert found["id"] != old["id"]


def test_life_repo_latest_user_event_handles_naive_existing_event_with_aware_now(life_repo: LifeRepo) -> None:
    event = life_repo.create_event(
        event_type="water",
        occurred_at="2026-05-29T10:00:00",
        source="wechat",
        raw_text="喝水了",
    )

    found = life_repo.latest_user_event(
        now="2026-05-29T10:10:00+08:00",
        window_minutes=30,
    )

    assert found is not None
    assert found["id"] == event["id"]


def test_life_repo_dedupe_reminder_logs(life_repo: LifeRepo) -> None:
    first = life_repo.create_reminder_log(
        reminder_type="water",
        scheduled_for="2026-05-29T10:00:00+08:00",
        status="sending",
        dedupe_key="water:2026-05-29:10:00",
    )
    second = life_repo.create_reminder_log(
        reminder_type="water",
        scheduled_for="2026-05-29T10:00:00+08:00",
        status="sending",
        dedupe_key="water:2026-05-29:10:00",
    )

    assert first["id"] == second["id"]
    assert life_repo.count_reminder_logs("water") == 1


def test_life_repo_lock_rejects_active_holder_and_allows_expired_holder(life_repo: LifeRepo) -> None:
    first = life_repo.acquire_lock(
        name="life_tick",
        owner="worker-a",
        now="2026-05-30T10:00:00+08:00",
        ttl_seconds=50,
    )
    second = life_repo.acquire_lock(
        name="life_tick",
        owner="worker-b",
        now="2026-05-30T10:00:10+08:00",
        ttl_seconds=50,
    )
    expired = life_repo.acquire_lock(
        name="life_tick",
        owner="worker-b",
        now="2026-05-30T10:01:00+08:00",
        ttl_seconds=50,
    )

    row = life_repo.conn.execute("SELECT owner FROM life_locks WHERE name = 'life_tick'").fetchone()
    assert first is True
    assert second is False
    assert expired is True
    assert row["owner"] == "worker-b"


def test_life_repo_lock_release_respects_owner(life_repo: LifeRepo) -> None:
    life_repo.acquire_lock(
        name="life_tick",
        owner="worker-a",
        now="2026-05-30T10:00:00+08:00",
        ttl_seconds=50,
    )

    wrong_owner = life_repo.release_lock(name="life_tick", owner="worker-b")
    right_owner = life_repo.release_lock(name="life_tick", owner="worker-a")

    assert wrong_owner is False
    assert right_owner is True
    assert life_repo.conn.execute("SELECT * FROM life_locks WHERE name = 'life_tick'").fetchone() is None
