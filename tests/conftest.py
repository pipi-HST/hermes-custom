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
def real_db():
    """指向真实 data/captures.db 的 repo（用于集成测试）。"""
    from pathlib import Path
    db_path = str(Path(__file__).parent.parent / "data" / "captures.db")
    conn = init_db(db_path)
    return CaptureRepo(conn)
