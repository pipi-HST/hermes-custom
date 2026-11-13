"""An interrupted captures rebuild must not silently empty the database."""
from __future__ import annotations

import sqlite3

import pytest

import db.schema as schema
from db.schema import CAPTURE_COLUMNS, CREATE_CAPTURES_TABLE, init_db


def _seed_leftover_captures_new(db_path: str, rows: list[tuple[str, str]]) -> None:
    """Recreate the state left behind by a crash inside the captures rebuild."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            CREATE_CAPTURES_TABLE.replace(
                "CREATE TABLE IF NOT EXISTS captures",
                "CREATE TABLE captures_new",
                1,
            )
        )
        conn.executemany(
            "INSERT INTO captures_new (id, file_path) VALUES (?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _capture_ids(db_path: str) -> dict[str, str]:
    conn = init_db(db_path)
    try:
        return {
            row["id"]: row["file_path"]
            for row in conn.execute("SELECT id, file_path FROM captures").fetchall()
        }
    finally:
        conn.close()


def test_leftover_captures_new_is_promoted_when_captures_is_missing(tmp_path):
    db_path = str(tmp_path / "recovery.db")
    init_db(db_path).close()

    # Crash window: rows already copied and ``captures`` already dropped.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE captures")
        conn.commit()
    finally:
        conn.close()
    _seed_leftover_captures_new(
        db_path,
        [("c1", "captures/c1.md"), ("c2", "captures/c2.md")],
    )

    assert _capture_ids(db_path) == {
        "c1": "captures/c1.md",
        "c2": "captures/c2.md",
    }


def test_leftover_captures_new_is_promoted_when_captures_is_empty(tmp_path):
    db_path = str(tmp_path / "recovery.db")
    init_db(db_path).close()

    # Crash window: the empty captures table was recreated but the copy was
    # never renamed back into place.
    _seed_leftover_captures_new(db_path, [("c1", "captures/c1.md")])

    conn = init_db(db_path)
    try:
        assert [row["id"] for row in conn.execute("SELECT id FROM captures")] == ["c1"]
        assert [row["name"] for row in conn.execute("PRAGMA table_info(captures)")] == (
            CAPTURE_COLUMNS
        )
    finally:
        conn.close()


def test_live_captures_rows_win_over_a_stale_captures_new(tmp_path):
    db_path = str(tmp_path / "recovery.db")
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("live", "captures/live.md"),
    )
    conn.commit()
    conn.close()

    _seed_leftover_captures_new(db_path, [("stale", "captures/stale.md")])

    assert _capture_ids(db_path) == {"live": "captures/live.md"}


def test_failed_migration_rolls_back_the_whole_schema_batch(tmp_path, monkeypatch):
    """init_db keeps DDL atomic: a crash leaves no half-created schema."""

    def explode(_conn) -> None:
        raise RuntimeError("simulated migration crash")

    monkeypatch.setattr(schema, "_migrate_review_items_table", explode)

    db_path = str(tmp_path / "atomic.db")
    with pytest.raises(RuntimeError, match="simulated migration crash"):
        schema.init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()
    assert {name for name in tables if not name.startswith("sqlite_")} == set()
