"""Timestamp format migration keeps legacy SQLite-default rows sortable."""
from __future__ import annotations

from db.schema import init_db


def test_legacy_space_timestamps_are_normalised(tmp_path):
    db_path = str(tmp_path / "ts.db")
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO captures (id, created_at, updated_at) VALUES (?, ?, ?)",
        ("legacy", "2026-01-02 03:04:05", "2026-01-02 03:04:05"),
    )
    conn.commit()
    conn.close()

    conn = init_db(db_path)
    row = conn.execute("SELECT created_at, updated_at FROM captures").fetchone()
    assert row["created_at"] == "2026-01-02T03:04:05Z"
    assert row["updated_at"] == "2026-01-02T03:04:05Z"
    conn.close()


def test_iso_timestamps_are_left_alone(tmp_path):
    db_path = str(tmp_path / "ts.db")
    conn = init_db(db_path)
    iso = "2026-01-02T03:04:05+00:00"
    conn.execute(
        "INSERT INTO captures (id, created_at, updated_at) VALUES (?, ?, ?)",
        ("iso", iso, iso),
    )
    conn.commit()
    conn.close()

    conn = init_db(db_path)
    row = conn.execute("SELECT created_at FROM captures WHERE id='iso'").fetchone()
    assert row["created_at"] == iso
    conn.close()


def test_migration_is_idempotent(tmp_path):
    db_path = str(tmp_path / "ts.db")
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO captures (id, created_at) VALUES (?, ?)",
        ("legacy", "2026-01-02 03:04:05"),
    )
    conn.commit()
    conn.close()

    for _ in range(3):
        conn = init_db(db_path)
        conn.close()

    conn = init_db(db_path)
    row = conn.execute("SELECT created_at FROM captures WHERE id='legacy'").fetchone()
    assert row["created_at"] == "2026-01-02T03:04:05Z"
    conn.close()
