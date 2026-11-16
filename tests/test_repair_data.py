"""Tests for the local data repair maintenance script."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db.embedding_repo import CaptureEmbeddingRepo
from db.schema import init_db
from scripts.repair_data import repair_data


def _write_note(
    vault_path: Path, rel_path: str, capture_id: str, title: str = "Test Note"
) -> None:
    note_path = vault_path / rel_path
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        "\n".join(
            [
                "---",
                f"id: {capture_id}",
                f"title: {title}",
                "---",
                "",
                "Body",
            ]
        ),
        encoding="utf-8",
    )


def _insert_capture(
    conn,
    capture_id: str,
    file_path: str,
    *,
    raw_path: str = "",
    created_at: str = "2026-05-27 12:00:00",
) -> None:
    conn.execute(
        """INSERT INTO captures
           (id, file_path, raw_path, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (capture_id, file_path, raw_path, created_at, created_at),
    )
    conn.commit()


def _insert_draft(
    conn,
    draft_id: str,
    *,
    status: str,
    committed_capture_id: str = "",
    title: str = "Draft",
    raw_text: str = "Raw text",
    draft_text: str = "Draft text",
    instruction: str = "Summarize",
    created_at: str = "2026-05-27 12:00:00",
) -> None:
    conn.execute(
        """INSERT INTO capture_drafts
           (id, raw_text, title, draft_text, instruction, status,
            committed_capture_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            draft_id,
            raw_text,
            title,
            draft_text,
            instruction,
            status,
            committed_capture_id,
            created_at,
            created_at,
        ),
    )
    conn.commit()


def test_duplicate_capture_keeps_frontmatter_id_and_repoints_committed_draft(tmp_path):
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    conn = init_db(str(db_path))
    _write_note(vault_path, "captures/test.md", "keep-me")
    _write_note(vault_path, "raw/test.md", "keep-me")
    _insert_capture(
        conn,
        "delete-me",
        "captures/test.md",
        raw_path="raw/test.md",
        created_at="2026-05-27 12:00:00",
    )
    _insert_capture(
        conn,
        "keep-me",
        "captures/test.md",
        raw_path="raw/test.md",
        created_at="2026-05-27 12:01:00",
    )
    _insert_draft(conn, "draft-1", status="committed", committed_capture_id="delete-me")

    result = repair_data(db_path, vault_path, apply=True)

    ids = {
        row[0]
        for row in conn.execute("SELECT id FROM captures").fetchall()
    }
    draft_capture_id = conn.execute(
        "SELECT committed_capture_id FROM capture_drafts WHERE id = 'draft-1'"
    ).fetchone()[0]
    assert ids == {"keep-me"}
    assert draft_capture_id == "keep-me"
    assert result["after"]["duplicate_capture_paths"] == 0


def test_repair_delete_removes_derived_rows(tmp_path):
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    conn = init_db(str(db_path))
    _write_note(vault_path, "captures/test.md", "keep-me")
    _insert_capture(conn, "delete-me", "captures/test.md")
    _insert_capture(conn, "keep-me", "captures/test.md")
    CaptureEmbeddingRepo(conn).upsert(
        capture_id="delete-me",
        kind="identity",
        section_id="",
        section_title="",
        index_text="duplicate body",
        embedding=b"\0" * 4096,
        source_hash="delete-me-identity",
        file_path="captures/test.md",
    )

    repair_data(db_path, vault_path, apply=True)

    assert conn.execute(
        "SELECT COUNT(*) FROM capture_embeddings WHERE capture_id = 'delete-me'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM captures WHERE id = 'delete-me'"
    ).fetchone()[0] == 0


def test_repair_apply_rolls_back_database_on_failure(tmp_path):
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    conn = init_db(str(db_path))
    _write_note(vault_path, "captures/test.md", "keep-me")
    _insert_capture(conn, "delete-me", "captures/test.md")
    _insert_capture(conn, "keep-me", "captures/test.md")
    conn.execute(
        """CREATE TRIGGER fail_delete
           BEFORE DELETE ON captures
           WHEN OLD.id = 'delete-me'
           BEGIN
               SELECT RAISE(ABORT, 'boom');
           END"""
    )

    with pytest.raises(sqlite3.IntegrityError):
        repair_data(db_path, vault_path, apply=True)

    ids = {row[0] for row in conn.execute("SELECT id FROM captures").fetchall()}
    assert ids == {"delete-me", "keep-me"}


def test_repair_data_apply_records_write_job(tmp_path):
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    conn = init_db(str(db_path))
    _write_note(vault_path, "captures/test.md", "keep-me")
    _insert_capture(conn, "delete-me", "captures/test.md")
    _insert_capture(conn, "keep-me", "captures/test.md")

    result = repair_data(db_path, vault_path, apply=True)

    job = conn.execute(
        "SELECT * FROM knowledge_write_jobs WHERE intent = 'repair_data'"
    ).fetchone()
    assert job["status"] == "completed"
    assert job["object_type"] == "repair_batch"
    assert job["file_path"] == result["report_path"]
    assert job["db_table"] == "captures,capture_drafts"


def test_repair_data_apply_empty_store_does_not_write_artifacts(tmp_path):
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    (vault_path / "captures").mkdir(parents=True)
    (vault_path / "_archive" / "raw").mkdir(parents=True)
    conn = init_db(str(db_path))

    result = repair_data(db_path, vault_path, apply=True)

    assert result["planned_actions"] == []
    assert result["missing_file_reports"] == []
    assert result["missing_file_deletions"] == []
    assert result["backup_path"] == ""
    assert result["report_path"] == ""
    assert not (tmp_path / "backups").exists()
    assert not (tmp_path / "repair_reports").exists()
    jobs = conn.execute(
        "SELECT count(*) FROM knowledge_write_jobs WHERE intent = 'repair_data'"
    ).fetchone()[0]
    assert jobs == 0


def test_dry_run_reports_duplicate_capture_without_changing_db(tmp_path):
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    conn = init_db(str(db_path))
    _write_note(vault_path, "captures/test.md", "keep-me")
    _insert_capture(conn, "delete-me", "captures/test.md")
    _insert_capture(conn, "keep-me", "captures/test.md")

    result = repair_data(db_path, vault_path, apply=False)

    ids = {
        row[0]
        for row in conn.execute("SELECT id FROM captures").fetchall()
    }
    assert ids == {"delete-me", "keep-me"}
    assert result["dry_run"] is True
    assert result["planned_actions"]


def test_duplicate_open_drafts_keep_latest_and_discard_older(tmp_path):
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    conn = init_db(str(db_path))
    _insert_draft(
        conn,
        "old-draft",
        status="draft_ready",
        created_at="2026-05-27 12:00:00",
    )
    _insert_draft(
        conn,
        "new-draft",
        status="draft_ready",
        created_at="2026-05-27 12:01:00",
    )

    result = repair_data(db_path, vault_path, apply=True)

    statuses = dict(
        conn.execute("SELECT id, status FROM capture_drafts").fetchall()
    )
    assert statuses == {"old-draft": "discarded", "new-draft": "draft_ready"}
    assert result["after"]["open_draft_duplicate_groups"] == 0


def test_missing_capture_file_with_frontmatter_id_elsewhere_is_report_only(tmp_path):
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    conn = init_db(str(db_path))
    _write_note(vault_path, "captures/elsewhere.md", "keep-reported")
    _insert_capture(conn, "keep-reported", "captures/missing.md")

    result = repair_data(db_path, vault_path, apply=True)

    ids = {
        row[0]
        for row in conn.execute("SELECT id FROM captures").fetchall()
    }
    assert ids == {"keep-reported"}
    assert result["missing_file_reports"]
    assert not result["missing_file_deletions"]


def test_missing_capture_file_without_any_frontmatter_id_is_deleted(tmp_path):
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    conn = init_db(str(db_path))
    _insert_capture(conn, "delete-missing", "captures/missing.md")

    result = repair_data(db_path, vault_path, apply=True)

    count = conn.execute("SELECT count(*) FROM captures").fetchone()[0]
    assert count == 0
    assert result["missing_file_deletions"]


def test_missing_capture_file_clears_committed_draft_pointer(tmp_path):
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    conn = init_db(str(db_path))
    _insert_capture(conn, "deleted-cap", "captures/missing.md")
    _insert_draft(
        conn,
        "draft-1",
        status="committed",
        committed_capture_id="deleted-cap",
    )

    result = repair_data(db_path, vault_path, apply=True)

    pointer = conn.execute(
        "SELECT committed_capture_id FROM capture_drafts WHERE id = 'draft-1'"
    ).fetchone()[0]
    assert pointer == ""
    assert result["missing_file_deletions"]


def test_missing_raw_file_keeps_capture_and_clears_raw_path(tmp_path):
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    conn = init_db(str(db_path))
    _write_note(vault_path, "captures/kept.md", "cap-1")
    _insert_capture(conn, "cap-1", "captures/kept.md", raw_path="raw/missing.md")

    result = repair_data(db_path, vault_path, apply=True)

    row = conn.execute("SELECT file_path, raw_path FROM captures WHERE id = 'cap-1'").fetchone()
    assert row["file_path"] == "captures/kept.md"
    assert row["raw_path"] == ""
    assert any(a["type"] == "clear_missing_raw_path" for a in result["planned_actions"])


def test_readable_rename_updates_capture_and_raw_paths(tmp_path):
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    conn = init_db(str(db_path))
    _write_note(vault_path, "captures/2026-05-28-generate.md", "abc123456789", "AI Harness深度解析：用/最便宜的模型？")
    _write_note(vault_path, "raw/2026-05-28-generate.md", "abc123456789", "AI Harness深度解析：用/最便宜的模型？")
    _insert_capture(
        conn,
        "abc123456789",
        "captures/2026-05-28-generate.md",
        raw_path="raw/2026-05-28-generate.md",
        created_at="2026-05-28 12:00:00",
    )

    result = repair_data(db_path, vault_path, apply=True, rename_readable=True)

    row = conn.execute("SELECT file_path, raw_path FROM captures WHERE id = 'abc123456789'").fetchone()
    assert row["file_path"] == "captures/AI Harness深度解析：用最便宜的模型-2026-05-28-abc12345.md"
    assert row["raw_path"] == "_archive/raw/AI Harness深度解析：用最便宜的模型-2026-05-28-abc12345.md"
    assert (vault_path / row["file_path"]).exists()
    assert (vault_path / row["raw_path"]).exists()
    assert not (vault_path / "captures/2026-05-28-generate.md").exists()
    assert not (vault_path / "raw/AI Harness深度解析：用最便宜的模型-2026-05-28-abc12345.md").exists()
    assert any(a["type"] == "rename_capture_file" for a in result["planned_actions"])


def test_readable_rename_preserves_archived_raw_folder(tmp_path):
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    conn = init_db(str(db_path))
    _write_note(vault_path, "captures/2026-05-28-generate.md", "abc123456789", "AI Harness深度解析")
    _write_note(vault_path, "_archive/raw/2026-05-28-generate.md", "abc123456789", "AI Harness深度解析")
    _insert_capture(
        conn,
        "abc123456789",
        "captures/2026-05-28-generate.md",
        raw_path="_archive/raw/2026-05-28-generate.md",
        created_at="2026-05-28 12:00:00",
    )

    repair_data(db_path, vault_path, apply=True, rename_readable=True)

    row = conn.execute("SELECT raw_path FROM captures WHERE id = 'abc123456789'").fetchone()
    assert row["raw_path"] == "_archive/raw/AI Harness深度解析-2026-05-28-abc12345.md"
    assert (vault_path / row["raw_path"]).exists()
    assert not (vault_path / "raw/AI Harness深度解析-2026-05-28-abc12345.md").exists()


def test_readable_rename_dry_run_does_not_move_files(tmp_path):
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    conn = init_db(str(db_path))
    _write_note(vault_path, "captures/old.md", "abc123456789", "中文标题")
    _insert_capture(conn, "abc123456789", "captures/old.md")

    result = repair_data(db_path, vault_path, apply=False, rename_readable=True)

    row = conn.execute("SELECT file_path FROM captures WHERE id = 'abc123456789'").fetchone()
    assert row["file_path"] == "captures/old.md"
    assert (vault_path / "captures/old.md").exists()
    assert any(a["type"] == "rename_capture_file" for a in result["planned_actions"])
