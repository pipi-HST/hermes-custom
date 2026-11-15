"""共享 fixtures — 使用真实迁移过的临时数据库."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from db.schema import init_db
from db.repo import CaptureRepo


def _symlink_supported() -> bool:
    """Windows only allows symlinks in Developer Mode / elevated shells."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target").mkdir()
            (root / "link").symlink_to(root / "target", target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


SYMLINKS_SUPPORTED = _symlink_supported()
requires_symlink = pytest.mark.skipif(
    not SYMLINKS_SUPPORTED,
    reason="filesystem does not support symlinks (enable Windows Developer Mode)",
)


# Cross-repo pipelines need a hermes-agent-main checkout next to this one.
RUNTIME_ROOT = Path(
    os.environ.get("HERMES_RUNTIME_ROOT", "/Users/pipi/Downloads/hermes-agent-main")
).expanduser()
requires_runtime = pytest.mark.skipif(
    not RUNTIME_ROOT.is_dir(),
    reason=f"hermes-agent-main checkout not found at {RUNTIME_ROOT} (set HERMES_RUNTIME_ROOT)",
)


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


# ---------------------------------------------------------------------------
# Browser e2e isolation
# ---------------------------------------------------------------------------
# These modules spawn a real Chromium child process.  On Windows the browser
# occasionally dies inside crashpad and takes the whole pytest process with it
# (exit 4294930435), which hides every other result.  They are opt-in.
BROWSER_TEST_FILES = {
    "test_knowledge_product_e2e.py",
    "test_reset_hermes_local_data.py",
    "test_reset_smoke.py",
}


def pytest_addoption(parser):
    parser.addoption(
        "--run-browser",
        action="store_true",
        default=False,
        help="also run the desktop-browser e2e tests (spawns Chromium)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-browser"):
        return
    skip_browser = pytest.mark.skip(reason="browser e2e: pass --run-browser to run")
    for item in items:
        if Path(str(item.fspath)).name in BROWSER_TEST_FILES:
            item.add_marker(skip_browser)
