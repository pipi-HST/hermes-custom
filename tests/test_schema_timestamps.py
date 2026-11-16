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


def test_rows_written_with_the_sql_default_are_normalised_at_write_time(tmp_path):
    """After the one-time migration, triggers normalise new rows immediately."""
    db_path = str(tmp_path / "ts.db")
    conn = init_db(db_path)
    conn.close()

    conn = init_db(db_path)
    conn.execute("INSERT INTO captures (id) VALUES ('fresh')")
    conn.commit()
    raw = conn.execute("SELECT created_at FROM captures WHERE id='fresh'").fetchone()
    assert "T" in raw["created_at"] and raw["created_at"].endswith("Z")
    conn.close()

    conn = init_db(db_path)
    row = conn.execute("SELECT created_at FROM captures WHERE id='fresh'").fetchone()
    assert row["created_at"] == raw["created_at"]
    conn.close()


def test_timestamp_marker_skips_repeat_scan(tmp_path, monkeypatch):
    from db import schema

    db_path = str(tmp_path / "ts.db")
    init_db(db_path).close()

    def _must_not_scan(_conn):
        raise AssertionError("timestamp migration should be marker-gated")

    monkeypatch.setattr(schema, "_migrate_timestamp_formats", _must_not_scan)

    conn = init_db(db_path)
    try:
        marker = conn.execute(
            "SELECT value FROM app_meta WHERE key = ?",
            (schema.TIMESTAMP_FORMAT_MARKER,),
        ).fetchone()
        assert marker["value"] == "1"
    finally:
        conn.close()


def test_unparseable_timestamps_are_kept_instead_of_nulled(tmp_path):
    """``strftime`` returns NULL for fake dates; the old SQL lost/aborted rows."""
    db_path = str(tmp_path / "ts.db")
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO captures "
        "(id, created_at, updated_at, last_reviewed, next_review) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "bad",
            "0000-00-00 00:00:00",
            "0000-00-00 00:00:00",
            "0000-00-00 00:00:00",
            "0000-00-00 00:00:00",
        ),
    )
    conn.commit()
    conn.close()

    # Must not raise on the NOT NULL created_at/updated_at columns.
    conn = init_db(db_path)
    row = conn.execute(
        "SELECT created_at, updated_at, last_reviewed, next_review FROM captures "
        "WHERE id='bad'"
    ).fetchone()
    assert row["created_at"] == "0000-00-00 00:00:00"
    assert row["updated_at"] == "0000-00-00 00:00:00"
    # Nullable columns keep their original value instead of silently turning NULL.
    assert row["last_reviewed"] == "0000-00-00 00:00:00"
    assert row["next_review"] == "0000-00-00 00:00:00"
    conn.close()
