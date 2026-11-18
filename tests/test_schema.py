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
        assert "embedding" not in cols
        assert "archived_at" in cols
        assert "review_state" not in cols
        assert "review_interval" not in cols
        assert "last_reviewed" not in cols
        assert "next_review" not in cols
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


def test_capture_embeddings_search_index_exists(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(capture_embeddings)").fetchall()
    }

    assert "idx_capture_embeddings_search" in indexes
    conn.close()


def test_agent_run_objects_table_exists(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(agent_run_objects)")]

    assert cols == [
        "id",
        "run_id",
        "object_type",
        "object_id",
        "role",
        "source",
        "created_at",
    ]
    conn.close()


def test_knowledge_write_jobs_table_exists(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(knowledge_write_jobs)")]

    assert cols == [
        "id",
        "intent",
        "object_type",
        "object_id",
        "status",
        "step",
        "file_path",
        "db_table",
        "payload_json",
        "error",
        "retry_count",
        "created_at",
        "updated_at",
    ]
    conn.close()


def test_knowledge_edges_table_exists(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(knowledge_edges)")]

    assert cols == [
        "id",
        "source_kind",
        "source_id",
        "target_kind",
        "target_id",
        "relation_type",
        "score",
        "evidence_json",
        "generated_by",
        "created_at",
        "updated_at",
    ]
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(knowledge_edges)").fetchall()
    }
    assert "idx_knowledge_edges_lookup" in indexes
    assert "idx_knowledge_edges_relation" in indexes
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
        "folder_path",
    ]
    conn.close()


def test_garden_drafts_table_exists(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(garden_drafts)")]

    assert cols == [
        "id",
        "title",
        "card_kind",
        "schema_version",
        "payload_json",
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


def test_garden_cards_table_has_card_kind(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(garden_cards)")]

    assert "card_kind" in cols
    row = conn.execute(
        "SELECT dflt_value FROM pragma_table_info('garden_cards') WHERE name='card_kind'"
    ).fetchone()
    assert row[0] == "'viewpoint'"
    conn.close()


def test_reading_session_tables_exist(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))

    session_cols = [
        row[1] for row in conn.execute("PRAGMA table_info(reading_sessions)")
    ]
    assert session_cols == [
        "id",
        "source_type",
        "source_id",
        "source_url",
        "source_title",
        "status",
        "focus",
        "expires_at",
        "committed_artifact_id",
        "meta_json",
        "created_at",
        "updated_at",
        "last_turn_at",
    ]

    turn_cols = [
        row[1] for row in conn.execute("PRAGMA table_info(reading_turns)")
    ]
    assert turn_cols == [
        "id",
        "session_id",
        "turn_index",
        "user_message",
        "assistant_answer",
        "focus",
        "confidence",
        "meta_json",
        "created_at",
    ]

    evidence_cols = [
        row[1] for row in conn.execute("PRAGMA table_info(reading_evidence)")
    ]
    assert evidence_cols == [
        "id",
        "session_id",
        "turn_id",
        "source_kind",
        "source_id",
        "title",
        "url",
        "excerpt",
        "used_for",
        "meta_json",
        "created_at",
    ]

    draft_cols = [
        row[1] for row in conn.execute("PRAGMA table_info(reading_drafts)")
    ]
    assert draft_cols == [
        "id",
        "session_id",
        "output_type",
        "target_capture_id",
        "title",
        "summary",
        "draft_text",
        "status",
        "meta_json",
        "created_at",
        "updated_at",
    ]

    backup_cols = [
        row[1] for row in conn.execute("PRAGMA table_info(capture_version_backups)")
    ]
    assert backup_cols == [
        "id",
        "capture_id",
        "reading_draft_id",
        "backup_file_path",
        "previous_file_hash",
        "new_file_hash",
        "meta_json",
        "created_at",
    ]
    conn.close()


def test_garden_drafts_table_has_card_kind(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(garden_drafts)")]

    assert "card_kind" in cols
    row = conn.execute(
        "SELECT dflt_value FROM pragma_table_info('garden_drafts') WHERE name='card_kind'"
    ).fetchone()
    assert row[0] == "'viewpoint'"
    conn.close()


def test_schema_migrates_legacy_capture_embedding_to_identity_index(tmp_path):
    import struct

    from agents.embedder import DIM
    from db.schema import init_db

    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    # Simulate a v2 database: the legacy column existed then.
    conn.execute("ALTER TABLE captures ADD COLUMN embedding BLOB")
    embedding = struct.pack(f"{DIM}f", *([1.0] + [0.0] * (DIM - 1)))
    conn.execute(
        "INSERT INTO captures (id, file_path, embedding) VALUES (?, ?, ?)",
        ("legacy-cap", "captures/legacy.md", embedding),
    )
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    conn = init_db(db_path)
    try:
        row = conn.execute(
            "SELECT kind, embedding FROM capture_embeddings WHERE capture_id = 'legacy-cap'"
        ).fetchone()
        columns = {
            item["name"]
            for item in conn.execute("PRAGMA table_info(captures)").fetchall()
        }
    finally:
        conn.close()

    assert row is not None
    assert row["kind"] == "identity"
    assert row["embedding"] == embedding
    assert "embedding" not in columns


def test_schema_normalizes_garden_v1_rows_to_v2(tmp_path):
    from db.schema import init_db

    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO garden_cards (id, title, schema_version) "
        "VALUES ('legacy-card', 't', 'garden_card.v1')"
    )
    conn.execute(
        "INSERT INTO garden_drafts (id, title, schema_version) "
        "VALUES ('legacy-draft', 't', 'garden_card.v1')"
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    conn = init_db(db_path)
    try:
        card = conn.execute(
            "SELECT schema_version FROM garden_cards WHERE id = 'legacy-card'"
        ).fetchone()[0]
        draft = conn.execute(
            "SELECT schema_version FROM garden_drafts WHERE id = 'legacy-draft'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert card == "garden_card.v2"
    assert draft == "garden_card.v2"


def test_schema_v4_moves_archive_and_garden_v1_fields_then_drops_them(tmp_path):
    import sqlite3

    from db.schema import init_db

    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE captures (
               id TEXT PRIMARY KEY,
               file_path TEXT NOT NULL DEFAULT '',
               review_state TEXT NOT NULL DEFAULT 'new',
               review_interval INTEGER NOT NULL DEFAULT 0,
               review_count INTEGER NOT NULL DEFAULT 0,
               last_reviewed TEXT,
               next_review TEXT
           )"""
    )
    conn.execute(
        "INSERT INTO captures (id, file_path, review_state) VALUES ('old', 'captures/old.md', 'archived')"
    )
    for column in ("last_reviewed", "next_review"):
        for suffix in ("insert", "update"):
            trigger = (
                f"AFTER INSERT ON captures"
                if suffix == "insert"
                else f'AFTER UPDATE OF "{column}" ON captures'
            )
            conn.execute(
                f'''CREATE TRIGGER "trg_norm_captures_{column}_{suffix}"
                    {trigger}
                    WHEN NEW."{column}" GLOB '????-??-?? ??:??:??*'
                    BEGIN
                        UPDATE captures SET "{column}" = NEW."{column}" WHERE rowid = NEW.rowid;
                    END'''
            )
    conn.execute(
        """CREATE TABLE garden_cards (
               id TEXT PRIMARY KEY,
               title TEXT NOT NULL DEFAULT '',
               one_liner TEXT NOT NULL DEFAULT '',
               card_kind TEXT NOT NULL DEFAULT 'viewpoint',
               schema_version TEXT NOT NULL DEFAULT 'garden_card.v1',
               payload_json TEXT NOT NULL DEFAULT '{}',
               core_arguments TEXT NOT NULL DEFAULT '[]',
               knowledge_links TEXT NOT NULL DEFAULT '[]',
               key_materials TEXT NOT NULL DEFAULT '[]',
               my_view TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL DEFAULT 'seed',
               capture_ids TEXT NOT NULL DEFAULT '[]',
               updated_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    conn.execute(
        "INSERT INTO garden_cards (id, title, one_liner, card_kind) VALUES ('card', '旧卡', '旧一句话', 'method')"
    )
    conn.commit()
    conn.close()

    conn = init_db(db_path)
    try:
        capture = conn.execute(
            "SELECT archived_at FROM captures WHERE id = 'old'"
        ).fetchone()
        card = conn.execute(
            "SELECT payload_json FROM garden_cards WHERE id = 'card'"
        ).fetchone()
        capture_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(captures)")
        }
        card_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(garden_cards)")
        }
    finally:
        conn.close()

    import json

    assert capture["archived_at"]
    assert json.loads(card["payload_json"])["claim"] == "旧一句话"
    assert not capture_columns & {
        "review_state",
        "review_interval",
        "review_count",
        "last_reviewed",
        "next_review",
    }
    assert not card_columns & {
        "one_liner",
        "core_arguments",
        "knowledge_links",
        "key_materials",
        "my_view",
    }


def test_schema_backfills_body_for_legacy_garden_card(tmp_path):
    import json
    import sqlite3

    from db.schema import init_db

    db_path = str(tmp_path / "legacy-card-body.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE garden_cards (
               id TEXT PRIMARY KEY,
               title TEXT NOT NULL DEFAULT '',
               one_liner TEXT NOT NULL DEFAULT '',
               card_kind TEXT NOT NULL DEFAULT 'viewpoint',
               schema_version TEXT NOT NULL DEFAULT 'garden_card.v1',
               payload_json TEXT NOT NULL DEFAULT '{}',
               core_arguments TEXT NOT NULL DEFAULT '[]',
               knowledge_links TEXT NOT NULL DEFAULT '[]',
               key_materials TEXT NOT NULL DEFAULT '[]',
               my_view TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL DEFAULT 'seed',
               capture_ids TEXT NOT NULL DEFAULT '[]',
               updated_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    conn.execute(
        "INSERT INTO garden_cards (id, title, one_liner, card_kind) "
        "VALUES ('card', '旧卡', '旧一句话', 'method')"
    )
    conn.commit()
    conn.close()

    conn = init_db(db_path)
    try:
        card = conn.execute(
            "SELECT content_markdown, payload_json FROM garden_cards WHERE id = 'card'"
        ).fetchone()
    finally:
        conn.close()

    body = card["content_markdown"]
    assert body
    assert "# 旧卡" in body
    assert "旧一句话" in body
    assert json.loads(card["payload_json"])["content_markdown"] == body


def test_reinit_promotes_column_only_card_body_into_payload(tmp_path):
    import json

    from db.schema import init_db

    db_path = str(tmp_path / "column-card-body.db")
    conn = init_db(db_path)
    conn.execute(
        """INSERT INTO garden_cards (id, title, content_markdown, payload_json)
           VALUES (?, ?, ?, ?)""",
        (
            "card-column-only",
            "列正文卡",
            "只有列里有完整正文。",
            json.dumps(
                {
                    "schema_version": "garden_card.v2",
                    "title": "列正文卡",
                    "claim": "列里的正文不能被迁移丢掉。",
                    "card_kind": "viewpoint",
                    "source_ids": [],
                },
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()
    conn.close()

    reopened = init_db(db_path)
    try:
        card = reopened.execute(
            "SELECT content_markdown, payload_json FROM garden_cards "
            "WHERE id = 'card-column-only'"
        ).fetchone()
    finally:
        reopened.close()

    body = card["content_markdown"]
    assert body == "只有列里有完整正文。"
    assert json.loads(card["payload_json"])["content_markdown"] == body


def test_reinit_renders_missing_card_body_from_structured_payload(tmp_path):
    import json

    from db.schema import init_db

    db_path = str(tmp_path / "structured-card-body.db")
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO garden_cards (id, title, payload_json) VALUES (?, ?, ?)",
        (
            "card-structured",
            "结构化卡",
            json.dumps(
                {
                    "schema_version": "garden_card.v2",
                    "title": "结构化卡",
                    "claim": "结构化正文不应变成空卡。",
                    "card_kind": "method",
                    "core_judgments": [
                        {
                            "statement": "旧数据只有结构化字段时也要渲染正文。",
                            "reasoning": "卡片正文是唯一读取入口。",
                        }
                    ],
                    "source_ids": [],
                },
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()
    conn.close()

    reopened = init_db(db_path)
    try:
        card = reopened.execute(
            "SELECT content_markdown, payload_json FROM garden_cards "
            "WHERE id = 'card-structured'"
        ).fetchone()
    finally:
        reopened.close()

    body = card["content_markdown"]
    assert body
    assert "# 结构化卡" in body
    assert "结构化正文不应变成空卡。" in body
    assert json.loads(card["payload_json"])["content_markdown"] == body
