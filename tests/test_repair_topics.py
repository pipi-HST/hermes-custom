"""Tests for the Topic/folder mirror repair script."""
from __future__ import annotations

import json

from db.schema import init_db
from scripts.repair_topics import repair_topics


def _seed(tmp_path):
    vault = tmp_path / "vault"
    note = vault / "captures" / "topic-note.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\n"
        "id: cap-topic\n"
        "title: Topic note\n"
        "folder_path: 深度研究\n"
        "---\n\n"
        "正文。\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "captures.db"
    conn = init_db(str(db_path))
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES ('cap-topic', 'captures/topic-note.md')"
    )
    conn.execute(
        """INSERT INTO capture_metadata
               (capture_id, folder_path, created_at, updated_at)
           VALUES ('cap-topic', 'Inbox', datetime('now'), datetime('now'))"""
    )
    conn.execute(
        """INSERT INTO garden_cards
               (id, title, source_theme, capture_ids, created_at, updated_at)
           VALUES ('card-topic', 'Topic card', 'Inbox', ?, datetime('now'), datetime('now'))""",
        (json.dumps(["cap-topic"]),),
    )
    conn.commit()
    conn.close()
    return db_path, vault


def test_repair_topics_dry_run_reports_without_writing(tmp_path):
    db_path, vault = _seed(tmp_path)

    report = repair_topics(db_path, vault, apply=False)

    assert report["mode"] == "dry-run"
    assert report["captures_scanned"] == 1
    assert report["metadata_updates"][0]["to"] == "深度研究"
    assert report["card_updates"][0]["to"] == "深度研究"
    assert report["applied"] is False

    conn = init_db(str(db_path))
    try:
        metadata = conn.execute(
            "SELECT folder_path FROM capture_metadata WHERE capture_id = 'cap-topic'"
        ).fetchone()
        card = conn.execute(
            "SELECT source_theme FROM garden_cards WHERE id = 'card-topic'"
        ).fetchone()
    finally:
        conn.close()
    assert metadata["folder_path"] == "Inbox"
    assert card["source_theme"] == "Inbox"


def test_repair_topics_apply_updates_metadata_and_card(tmp_path):
    db_path, vault = _seed(tmp_path)

    report = repair_topics(db_path, vault, apply=True)

    assert report["applied"] is True
    conn = init_db(str(db_path))
    try:
        metadata = conn.execute(
            "SELECT folder_path FROM capture_metadata WHERE capture_id = 'cap-topic'"
        ).fetchone()
        card = conn.execute(
            "SELECT source_theme FROM garden_cards WHERE id = 'card-topic'"
        ).fetchone()
    finally:
        conn.close()
    assert metadata["folder_path"] == "深度研究"
    assert card["source_theme"] == "深度研究"
