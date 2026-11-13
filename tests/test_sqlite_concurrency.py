"""Regression tests for shared-connection SQLite concurrency.

These cover the race that produced ``cannot start a transaction within a
transaction`` and silently discarded another thread's committed row when the
API shared one connection across the FastAPI threadpool.
"""
from __future__ import annotations

import threading
import time

import pytest

from db.concurrency import transaction
from db.repo import CaptureDraftRepo, CaptureRepo
from db.schema import init_db


@pytest.fixture
def conn(tmp_path):
    connection = init_db(str(tmp_path / "concurrency.db"))
    yield connection
    connection.close()


def test_concurrent_inserts_and_commits_do_not_lose_rows(conn):
    repo = CaptureRepo(conn)

    def worker(index: int) -> None:
        for _ in range(20):
            repo.insert(file_path=f"captures/{index}.md")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    rows = conn.execute("SELECT count(*) FROM captures").fetchone()[0]
    assert rows == 8 * 20


def test_reserve_does_not_fail_while_another_thread_writes(conn):
    repo = CaptureDraftRepo(conn)
    draft = repo.create(raw_text="x", draft_text="body", status="draft_ready")
    repo.claim_for_commit(draft["id"]) if hasattr(repo, "claim_for_commit") else None
    conn.execute(
        "UPDATE capture_drafts SET status='committing' WHERE id=?",
        (draft["id"],),
    )

    errors: list[str] = []
    stop = threading.Event()

    def noisy_writer() -> None:
        while not stop.is_set():
            try:
                conn.execute(
                    "INSERT INTO change_log (entity_type, entity_id, field, source) VALUES (?,?,?,?)",
                    ("capture", "c1", "body", "api"),
                )
            except Exception as exc:  # noqa: BLE001 - test records failures
                errors.append(f"{type(exc).__name__}: {exc}")

    writer = threading.Thread(target=noisy_writer)
    writer.start()
    try:
        time.sleep(0.05)
        capture_id = repo.reserve_capture_for_commit(draft["id"])
    finally:
        stop.set()
        writer.join()

    assert errors == []
    assert capture_id
    row = conn.execute(
        "SELECT pending_capture_id FROM capture_drafts WHERE id=?",
        (draft["id"],),
    ).fetchone()
    assert row["pending_capture_id"] == capture_id


def test_transaction_rolls_back_only_its_own_work(conn):
    repo = CaptureRepo(conn)

    with pytest.raises(RuntimeError):
        with transaction(conn):
            repo.insert(file_path="captures/first.md")
            raise RuntimeError("boom")

    # The rolled-back insert is gone.
    assert conn.execute("SELECT count(*) FROM captures").fetchone()[0] == 0

    repo.insert(file_path="captures/second.md")
    assert conn.execute("SELECT count(*) FROM captures").fetchone()[0] == 1


def test_nested_transaction_uses_savepoint(conn):
    repo = CaptureRepo(conn)

    with transaction(conn):
        repo.insert(file_path="captures/outer.md")
        with transaction(conn):
            repo.insert(file_path="captures/inner.md")
        assert conn.execute("SELECT count(*) FROM captures").fetchone()[0] == 2

    assert conn.execute("SELECT count(*) FROM captures").fetchone()[0] == 2
