"""Tests for Reading Session repository."""
from __future__ import annotations

from db.reading_repo import ReadingRepo
from db.schema import init_db


def test_create_session_defaults_to_seven_day_expiry(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    repo = ReadingRepo(conn)

    session = repo.create_session(
        source_type="capture",
        source_id="cap1",
        source_title="测试文章",
        focus="Terminal-Bench",
        meta={"confidence": 0.8},
    )

    assert session["id"]
    assert session["source_type"] == "capture"
    assert session["source_id"] == "cap1"
    assert session["source_title"] == "测试文章"
    assert session["status"] == "active"
    assert session["focus"] == "Terminal-Bench"
    assert session["expires_at"]
    assert session["meta_json"] == {"confidence": 0.8}


def test_add_turn_and_evidence_updates_session(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    repo = ReadingRepo(conn)
    session = repo.create_session(source_type="url", source_url="https://example.com")

    turn = repo.add_turn(
        session["id"],
        user_message="这段什么意思？",
        assistant_answer="这里是在解释评测不是终点。",
        focus="Eval",
        confidence="medium",
    )
    evidence = repo.add_evidence(
        session_id=session["id"],
        turn_id=turn["id"],
        source_kind="original",
        title="原文",
        excerpt="Eval 只是开始。",
        used_for="解释用户卡住的段落",
    )

    assert turn["turn_index"] == 1
    assert evidence["source_kind"] == "original"
    assert evidence["excerpt"] == "Eval 只是开始。"
    assert repo.get_session(session["id"])["last_turn_at"]
    assert repo.list_turns(session["id"])[0]["assistant_answer"] == "这里是在解释评测不是终点。"
    assert repo.list_evidence(session["id"])[0]["used_for"] == "解释用户卡住的段落"


def test_create_reading_draft_and_mark_committed(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    repo = ReadingRepo(conn)
    session = repo.create_session(source_type="capture", source_id="cap1")

    draft = repo.create_draft(
        session_id=session["id"],
        output_type="enhance_capture",
        target_capture_id="cap1",
        title="增强笔记",
        summary="讨论补充",
        draft_text="# 增强笔记\n\n正文",
    )

    assert draft["status"] == "open"
    assert draft["output_type"] == "enhance_capture"
    updated = repo.update_draft(draft["id"], status="committed")
    assert updated["status"] == "committed"


def test_record_capture_backup(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    repo = ReadingRepo(conn)

    backup = repo.record_capture_backup(
        capture_id="cap1",
        reading_draft_id="draft1",
        backup_file_path="data/backups/cap1.md",
        previous_file_hash="old",
        new_file_hash="new",
    )

    assert backup["capture_id"] == "cap1"
    assert backup["reading_draft_id"] == "draft1"
    assert backup["backup_file_path"] == "data/backups/cap1.md"
