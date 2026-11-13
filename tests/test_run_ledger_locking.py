"""run_with_ledger must not hold the connection lock across the operation.

The ledger used to wrap the whole bridge operation in ``connection_lock``, so a
slow LLM pipeline (summarize 300s / outliner 120s) queued every API read behind
it.  Only logical runs may be serialised, not the shared connection.
"""
from __future__ import annotations

import threading
import time

from bridge.result_envelope import success_envelope
from bridge.run_ledger import run_with_ledger
from db.agent_run_repo import AgentRunRepo
from db.schema import init_db


def test_slow_operation_does_not_block_other_statements(tmp_path):
    conn = init_db(str(tmp_path / "runs.db"))
    operation_started = threading.Event()
    operation_finished = threading.Event()
    errors: list[BaseException] = []

    def operation():
        operation_started.set()
        time.sleep(0.6)
        operation_finished.set()
        return success_envelope(
            agent="summarize",
            intent="summarize_capture",
            chat_preview="ok",
        )

    def run() -> None:
        try:
            run_with_ledger(
                conn,
                agent="summarize",
                intent="summarize_capture",
                operation=operation,
            )
        except BaseException as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert operation_started.wait(timeout=2)
        started_at = time.perf_counter()
        conn.execute("SELECT 1").fetchone()
        elapsed = time.perf_counter() - started_at
        assert elapsed < 0.2, f"read blocked for {elapsed:.3f}s behind the run"
        # The probe really overlapped the operation instead of racing its end.
        assert not operation_finished.is_set()
    finally:
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert errors == []
    repo = AgentRunRepo(conn)
    latest = repo.latest(limit=1)[0]
    assert latest["status"] == "success"
    assert [event["event_type"] for event in repo.events(latest["id"])] == [
        "started",
        "completed",
    ]
    conn.close()
