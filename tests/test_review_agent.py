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


def test_review_items_schema_tracks_view_counts(repo):
    columns = {
        row[1] for row in repo.conn.execute("PRAGMA table_info(review_items)").fetchall()
    }
    assert "view_count" in columns
    assert "last_viewed" in columns


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


def test_daily_review_creates_one_item_for_short_capture(repo, tmp_path):
    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    body = "# 短文\n\n这篇讲 AI 落地首先要从具体业务流程开始。"
    (captures_dir / "short.md").write_text(
        "---\n"
        "id: cap-short\n"
        "title: AI 落地短文\n"
        "story: 从业务流程开始。\n"
        "---\n\n"
        f"{body}",
        encoding="utf-8",
    )
    repo.conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("cap-short", "captures/short.md"),
    )
    repo.conn.commit()

    agent = ReviewAgent(repo, vault_path=str(tmp_path), long_note_chars=500)
    result = agent.run_daily()

    items = repo.conn.execute("SELECT * FROM review_items").fetchall()
    assert len(items) == 1
    assert items[0]["capture_id"] == "cap-short"
    assert items[0]["section_id"] == ""
    assert "AI 落地短文" in items[0]["question"]
    explanations = [m for m in result if m["type"] == "review_explanation"]
    assert len(explanations) == 1
    assert "复习问题" not in explanations[0]["text"]
    assert explanations[0]["view_count"] == 1
    assert explanations[0]["capture_view_count"] == 1

    updated_item = repo.conn.execute(
        "SELECT view_count, last_viewed FROM review_items WHERE capture_id = ?",
        ("cap-short",),
    ).fetchone()
    assert updated_item["view_count"] == 1
    assert updated_item["last_viewed"]
    updated_capture = repo.get_by_id("cap-short")
    assert updated_capture["review_count"] == 1


def test_daily_review_creates_section_items_for_long_capture(repo, tmp_path):
    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    body = (
        "# 长文\n\n"
        "## 方法论\n\n"
        "方法论部分强调先定位真实约束，再选择工具和组织方式。\n\n"
        "## 案例\n\n"
        "案例部分说明不同团队采用 AI 的阻力并不一样。\n"
    )
    (captures_dir / "long.md").write_text(
        "---\n"
        "id: cap-long\n"
        "title: 企业 AI 长文\n"
        "story: AI 落地方法论。\n"
        "---\n\n"
        f"{body}",
        encoding="utf-8",
    )
    repo.conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("cap-long", "captures/long.md"),
    )
    repo.conn.commit()

    ReviewAgent(repo, vault_path=str(tmp_path), long_note_chars=20).run_daily()

    rows = repo.conn.execute(
        "SELECT section_id, title, question FROM review_items ORDER BY section_id"
    ).fetchall()
    assert [(r["section_id"], r["title"]) for r in rows] == [
        ("section-1", "方法论"),
        ("section-2", "案例"),
    ]
    assert rows[0]["question"] == "方法论"


def test_record_item_feedback_updates_schedule(repo):
    repo.conn.execute(
        """INSERT INTO review_items
           (id, object_type, object_id, capture_id, title, question, answer_hint,
            review_interval, next_review)
           VALUES (?, 'capture', ?, ?, ?, ?, ?, 1, ?)""",
        (
            "ri1",
            "cap1",
            "cap1",
            "测试",
            "问题",
            "提示",
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    repo.conn.commit()

    agent = ReviewAgent(repo)
    agent.record_item_feedback("ri1", "useful")
    remembered = repo.conn.execute(
        "SELECT review_interval, review_count, review_state FROM review_items WHERE id='ri1'"
    ).fetchone()
    assert remembered["review_interval"] == 14
    assert remembered["review_count"] == 1
    assert remembered["review_state"] == "seen"

    agent.record_item_feedback("ri1", "revisit")
    revisit = repo.conn.execute(
        "SELECT review_interval, review_state FROM review_items WHERE id='ri1'"
    ).fetchone()
    assert revisit["review_interval"] == 3
    assert revisit["review_state"] == "revisit"

    agent.record_item_feedback("ri1", "mute")
    muted = repo.conn.execute(
        "SELECT review_state, next_review FROM review_items WHERE id='ri1'"
    ).fetchone()
    assert muted["review_state"] == "muted"
    assert muted["next_review"] is None


def test_daily_review_preview_includes_item_ids():
    from bridge.review_bridge import _format_daily_review_preview

    preview = _format_daily_review_preview([
        {
            "type": "review_explanation",
            "item_id": "ri1",
            "title": "企业 AI 长文",
            "topic": "方法论",
            "source_path": "captures/a.md",
            "view_count": 2,
            "capture_view_count": 5,
            "text": "详细讲解：方法论不是工具清单，而是判断约束的顺序。",
        }
    ])

    assert "今日回顾：方法论" in preview
    assert "这个点已讲过 2 次" in preview
    assert "这篇材料已被回顾 5 次" in preview
    assert "复习项：ri1" in preview
    assert "详细讲解" in preview


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
