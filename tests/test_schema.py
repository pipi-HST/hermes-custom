"""Test that the DB schema has been slimmed down to only essential columns."""


def test_captures_schema_has_slim_columns():
    import sqlite3
    import tempfile
    from pathlib import Path
    from db.schema import init_db

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        conn = init_db(db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(captures)").fetchall()]

        # New slim columns
        assert "id" in cols
        assert "file_path" in cols
        assert "raw_path" in cols
        assert "embedding" in cols
        assert "review_state" in cols
        assert "review_interval" in cols
        assert "last_reviewed" in cols
        assert "next_review" in cols
        assert "theme_scanned_at" in cols
        assert "created_at" in cols
        assert "updated_at" in cols

        # Removed columns
        assert "title" not in cols
        assert "summary" not in cols
        assert "tags" not in cols
        assert "source_url" not in cols
        assert "source_type" not in cols
        assert "cached_text" not in cols
        assert "raw_text" not in cols
        assert "word_count" not in cols
        assert "outline" not in cols

        # Legacy feishu columns never existed in this schema
        assert "feishu_doc_id" not in cols
        assert "feishu_doc_url" not in cols
        assert "raw_doc_url" not in cols
        assert "feishu_folder" not in cols
        conn.close()


def test_repo_insert_and_update_with_file_path():
    import tempfile
    from pathlib import Path
    from db.schema import init_db
    from db.repo import CaptureRepo

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        conn = init_db(db_path)
        repo = CaptureRepo(conn)

        # Insert with file_path and raw_path
        capture = repo.insert(
            file_path="captures/2026-05-26-test.md",
            raw_path="raw/2026-05-26-test.md",
        )

        assert capture["id"] is not None
        assert capture["file_path"] == "captures/2026-05-26-test.md"
        assert capture["raw_path"] == "raw/2026-05-26-test.md"

        # Update file_path
        repo.update(capture["id"], file_path="captures/2026-05-26-updated.md")
        updated = repo.get_by_id(capture["id"])
        assert updated["file_path"] == "captures/2026-05-26-updated.md"

        # Verify no feishu columns returned
        assert "feishu_doc_id" not in updated
        assert "feishu_doc_url" not in updated
        conn.close()


def test_capture_drafts_schema_exists():
    import tempfile
    from pathlib import Path
    from db.schema import init_db

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        conn = init_db(db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(capture_drafts)").fetchall()]

        assert "id" in cols
        assert "raw_text" in cols
        assert "draft_text" in cols
        assert "instruction" in cols
        assert "summary" in cols
        assert "tags" in cols
        assert "status" in cols
        assert "committed_capture_id" in cols
        conn.close()


def test_capture_embeddings_table_exists(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(capture_embeddings)")]

    assert cols == [
        "id",
        "capture_id",
        "kind",
        "section_id",
        "section_title",
        "index_text",
        "embedding",
        "source_hash",
        "file_path",
        "created_at",
        "updated_at",
    ]
    conn.close()


def test_capture_metadata_table_exists(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(capture_metadata)")]

    assert cols == [
        "capture_id",
        "outline_json",
        "outline_source_hash",
        "directory_rendered_at",
        "metadata_json",
        "created_at",
        "updated_at",
    ]
    conn.close()


def test_garden_drafts_table_exists(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(garden_drafts)")]

    assert cols == [
        "id",
        "title",
        "one_liner",
        "core_arguments",
        "knowledge_links",
        "key_materials",
        "markdown",
        "capture_ids",
        "question_id",
        "instruction",
        "status",
        "committed_card_id",
        "created_at",
        "updated_at",
    ]
    conn.close()
