"""Tests for CaptureRepo."""
from __future__ import annotations

import pytest
from db.schema import init_db
from db.repo import CaptureDraftRepo, CaptureRepo


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    return CaptureRepo(conn)


def test_insert_and_retrieve(repo):
    result = repo.insert(
        file_path="captures/test-note.md",
        raw_path="raw/test-note.md",
    )
    assert result is not None
    assert result["file_path"] == "captures/test-note.md"
    assert result["raw_path"] == "raw/test-note.md"
    assert result["id"] is not None


def test_list_all_returns_all(repo):
    repo.insert(file_path="captures/old.md")
    repo.insert(file_path="captures/new.md")
    items = repo.list_all()
    paths = {i["file_path"] for i in items}
    assert paths == {"captures/old.md", "captures/new.md"}


def test_update(repo):
    result = repo.insert(file_path="captures/before.md")
    updated = repo.update(result["id"], file_path="captures/after.md")
    assert updated["file_path"] == "captures/after.md"


def test_change_log(repo):
    result = repo.insert(file_path="captures/test.md")
    repo.log_change("capture", result["id"], "file_path",
                    "captures/test.md", "captures/updated.md", "agent")
    conn = repo.conn
    row = conn.execute(
        "SELECT * FROM change_log WHERE entity_id = ?",
        (result["id"],),
    ).fetchone()
    assert row is not None
    assert row["new_value"] == "captures/updated.md"


@pytest.fixture
def draft_repo(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    return CaptureDraftRepo(conn)


def test_create_and_get_capture_draft(draft_repo):
    draft = draft_repo.create(
        title="AI 时代的电力协议",
        raw_text="原文内容",
        source="https://example.com/article",
        source_type="link",
        word_count=4,
    )

    fetched = draft_repo.get_by_id(draft["id"])
    assert fetched is not None
    assert fetched["title"] == "AI 时代的电力协议"
    assert fetched["raw_text"] == "原文内容"
    assert fetched["status"] == "raw_extracted"


def test_update_capture_draft_and_get_latest_open(draft_repo):
    first = draft_repo.create(title="旧草稿", raw_text="旧内容")
    latest = draft_repo.create(title="新草稿", raw_text="新内容")

    draft_repo.update(
        latest["id"],
        draft_text="提炼后的内容",
        instruction="总结全文",
        status="draft_ready",
        summary="摘要",
        tags=["ai", "energy"],
    )

    open_draft = draft_repo.get_latest_open()
    assert open_draft["id"] == latest["id"]
    assert open_draft["draft_text"] == "提炼后的内容"
    assert open_draft["tags"] == ["ai", "energy"]

    draft_repo.update(latest["id"], status="committed", committed_capture_id="abc123")
    open_draft = draft_repo.get_latest_open()
    assert open_draft["id"] == first["id"]


def test_get_latest_open_prefers_latest_created_draft_over_updated_old_draft(draft_repo):
    old = draft_repo.create(title="旧草稿", raw_text="旧内容")
    new = draft_repo.create(title="新草稿", raw_text="新内容")

    draft_repo.conn.execute(
        "UPDATE capture_drafts SET created_at = ?, updated_at = ? WHERE id = ?",
        ("2026-05-26 12:01:00", "2026-05-26T12:05:29.134451+00:00", old["id"]),
    )
    draft_repo.conn.execute(
        "UPDATE capture_drafts SET created_at = ?, updated_at = ? WHERE id = ?",
        ("2026-05-26 12:04:00", "2026-05-26 12:04:00", new["id"]),
    )
    draft_repo.conn.commit()

    open_draft = draft_repo.get_latest_open()
    assert open_draft["id"] == new["id"]
