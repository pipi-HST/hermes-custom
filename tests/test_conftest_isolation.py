"""The ``real_db`` fixture must never point at the project database."""
from __future__ import annotations

import sqlite3
from pathlib import Path

LIVE_DB = Path(__file__).parent.parent / "data" / "captures.db"


def test_real_db_writes_do_not_reach_the_project_database(real_db):
    marker = "fixture-isolation-probe"
    real_db.conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        (marker, "captures/probe.md"),
    )
    real_db.conn.commit()

    # The fixture's own connection sees the row ...
    assert real_db.get_by_id(marker) is not None
    # ... but the live database on disk must not.
    if not LIVE_DB.is_file():
        return
    live = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    try:
        row = live.execute(
            "SELECT 1 FROM captures WHERE id = ?", (marker,)
        ).fetchone()
    finally:
        live.close()
    assert row is None
