"""共享 fixtures — 使用真实迁移过的临时数据库."""
from __future__ import annotations

import pytest
from db.schema import init_db
from db.repo import CaptureRepo


@pytest.fixture
def repo(tmp_path):
    """CaptureRepo with fully migrated temp DB."""
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    return CaptureRepo(conn)


@pytest.fixture
def real_db(tmp_path):
    """A copy of the real data/captures.db, never the live database.

    The fixture used to open (and migrate) the project database in place, so a
    test that wrote through it could mutate real captures.  Copy through the
    SQLite backup API instead - it also captures WAL content - and close the
    connection afterwards.
    """
    import sqlite3
    from pathlib import Path

    source = Path(__file__).parent.parent / "data" / "captures.db"
    if not source.is_file():
        pytest.skip("no local captures database to copy")
    db_path = str(tmp_path / "copy.db")
    source_conn = sqlite3.connect(str(source))
    try:
        target_conn = sqlite3.connect(db_path)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source_conn.close()
    conn = init_db(db_path)
    try:
        yield CaptureRepo(conn)
    finally:
        conn.close()
