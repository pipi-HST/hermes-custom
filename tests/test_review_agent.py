"""Tests for Review Agent."""
from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from db.schema import init_db
from db.repo import CaptureRepo
from agents.review_agent import (
    ReviewAgent, is_due, next_interval, INTERVAL_HOURS,
)


@pytest.fixture
def repo(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    return CaptureRepo(conn)


def test_next_interval_progression():
    assert next_interval("1d") == "3d"
    assert next_interval("3d") == "1w"
    assert next_interval("1w") == "2w"
    assert next_interval("2w") == "1m"
    assert next_interval("1m") == "mastered"
    assert next_interval("mastered") == "mastered"


def test_is_due_with_no_last_review():
    assert is_due(None, "1d") is True


def test_is_due_overdue():
    yesterday = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    assert is_due(yesterday, "1d") is True


def test_is_due_not_yet():
    hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert is_due(hour_ago, "1d") is False


def test_is_due_mastered():
    assert is_due(None, "mastered") is False
    assert is_due("2020-01-01T00:00:00+00:00", "mastered") is False


def test_review_agent_daily(repo):
    import json
    # Insert a Garden Card that's due
    repo.conn.execute(
        """INSERT INTO garden_cards (id, title, one_liner, core_arguments,
           knowledge_links, key_materials, status, capture_ids, review_interval)
           VALUES (?, ?, ?, '[]', '[]', '[]', ?, '[]', ?)""",
        ("gc01", "测试卡片", "核心结论", "growing", "1d"),
    )
    repo.conn.commit()

    # Insert some captures to sample
    for i in range(5):
        repo.insert(file_path=f"captures/test{i}.md")

    agent = ReviewAgent(repo)
    result = agent.run_daily()

    # Should have garden_review + capture_review + daily_summary
    types = [m["type"] for m in result]
    assert "garden_review" in types

    # Verify it's not empty
    assert len(result) > 0


def test_handle_reply_confirmed(repo):
    repo.conn.execute(
        """INSERT INTO garden_cards (id, title, one_liner, core_arguments,
           knowledge_links, key_materials, status, capture_ids, review_interval)
           VALUES (?, ?, ?, '[]', '[]', '[]', 'growing', '[]', '1d')""",
        ("gc01", "测试", "结论"),
    )
    repo.conn.commit()

    agent = ReviewAgent(repo)
    agent.handle_reply("gc01", "confirmed")

    row = repo.conn.execute(
        "SELECT * FROM garden_cards WHERE id='gc01'"
    ).fetchone()
    assert row["review_interval"] == "3d"
    assert row["skip_count"] in (0, None)


def test_handle_reply_forgot_resets(repo):
    repo.conn.execute(
        """INSERT INTO garden_cards (id, title, one_liner, core_arguments,
           knowledge_links, key_materials, status, capture_ids, review_interval)
           VALUES (?, ?, ?, '[]', '[]', '[]', 'growing', '[]', '3d')""",
        ("gc01", "测试", "结论"),
    )
    repo.conn.commit()

    agent = ReviewAgent(repo)
    agent.handle_reply("gc01", "forgot")

    row = repo.conn.execute(
        "SELECT * FROM garden_cards WHERE id='gc01'"
    ).fetchone()
    assert row["review_interval"] == "1d"


def test_handle_reply_skip_three_times_archives(repo):
    repo.conn.execute(
        """INSERT INTO garden_cards (id, title, one_liner, core_arguments,
           knowledge_links, key_materials, status, capture_ids, review_interval)
           VALUES (?, ?, ?, '[]', '[]', '[]', 'growing', '[]', '1d')""",
        ("gc01", "测试", "结论"),
    )
    repo.conn.commit()

    agent = ReviewAgent(repo)
    for _ in range(3):
        agent.handle_reply("gc01", "skip")

    row = repo.conn.execute(
        "SELECT * FROM garden_cards WHERE id='gc01'"
    ).fetchone()
    assert row["status"] == "archived"


def test_mark_capture_reviewed(repo):
    result = repo.insert(file_path="captures/test.md")
    agent = ReviewAgent(repo)
    agent.mark_capture_reviewed(result["id"])
    updated = repo.get_by_id(result["id"])
    assert updated["review_count"] >= 1


def test_hydrate_reads_title_from_frontmatter(repo, tmp_path):
    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    md_content = (
        "---\n"
        "id: test123\n"
        "title: 测试标题\n"
        "story: 这是一句摘要\n"
        "---\n"
        "正文内容\n"
    )
    (captures_dir / "2026-05-27-test.md").write_text(md_content)

    agent = ReviewAgent(repo, vault_path=str(tmp_path))
    row = {"id": "test123", "file_path": "captures/2026-05-27-test.md"}
    result = agent._hydrate(row)

    assert result["title"] == "测试标题"
    assert result["story"] == "这是一句摘要"


def test_hydrate_returns_row_unchanged_when_no_vault_path(repo):
    agent = ReviewAgent(repo, vault_path="")
    row = {"id": "test123", "file_path": "captures/test.md"}
    result = agent._hydrate(row)
    assert result == row


def test_hydrate_returns_row_unchanged_when_file_missing(repo, tmp_path):
    (tmp_path / "captures").mkdir()
    agent = ReviewAgent(repo, vault_path=str(tmp_path))
    row = {"id": "test123", "file_path": "captures/does-not-exist.md"}
    result = agent._hydrate(row)
    assert result == row

