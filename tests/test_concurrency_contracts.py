from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import pytest

from agents.capture_agent import CaptureAgent, CaptureConfig
from bridge.result_envelope import success_envelope
from bridge.run_ledger import run_with_ledger
from db.schema import init_db


def test_run_with_ledger_serializes_operations_on_shared_connection(tmp_path):
    conn = init_db(str(tmp_path / "runs.db"))
    first_entered = Event()
    release_first = Event()
    second_entered = Event()
    order_lock = Lock()
    operation_count = 0

    def operation():
        nonlocal operation_count
        with order_lock:
            operation_count += 1
            position = operation_count
        if position == 1:
            first_entered.set()
            assert release_first.wait(timeout=2)
        else:
            second_entered.set()
        return success_envelope(
            agent="capture",
            intent="concurrency_probe",
            chat_preview="ok",
        )

    def run():
        return run_with_ledger(
            conn,
            agent="capture",
            intent="concurrency_probe",
            operation=operation,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run)
        assert first_entered.wait(timeout=2)
        second = executor.submit(run)
        assert not second_entered.wait(timeout=0.2)
        release_first.set()
        assert first.result(timeout=2)["status"] == "success"
        assert second.result(timeout=2)["status"] == "success"


def test_concurrent_capture_commit_creates_only_one_capture(tmp_path):
    config = CaptureConfig(
        llm_api_key="sk-test",
        qwen_api_key="",
        vault_path=str(tmp_path / "vault"),
        db_path=str(tmp_path / "captures.db"),
    )
    first_agent = CaptureAgent(config)
    second_agent = CaptureAgent(config)
    draft = first_agent.create_editable_draft(
        "并发提交只能生成一份正式知识文件。",
        title="并发提交",
        organize=False,
    )
    barrier = Barrier(2)

    for agent in (first_agent, second_agent):
        original = agent._finalize_draft_before_commit

        def synchronized_finalize(value, *, _original=original):
            barrier.wait(timeout=2)
            return _original(value)

        agent._finalize_draft_before_commit = synchronized_finalize

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda agent: agent.commit_draft(draft["id"]),
                (first_agent, second_agent),
            )
        )

    committed = [item for item in results if item and item.get("status") == "committed"]
    rejected = [item for item in results if item and item.get("error")]
    capture_count = first_agent.conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
    files = list((tmp_path / "vault" / "captures").glob("*.md"))

    assert len(committed) == 1
    assert len(rejected) == 1
    assert capture_count == 1
    assert len(files) == 1


def test_failed_capture_commit_restores_claimed_draft_status(tmp_path, monkeypatch):
    config = CaptureConfig(
        llm_api_key="sk-test",
        qwen_api_key="",
        vault_path=str(tmp_path / "vault"),
        db_path=str(tmp_path / "captures.db"),
    )
    agent = CaptureAgent(config)
    draft = agent.create_editable_draft(
        "提交失败后应该允许重试。",
        title="失败恢复",
        organize=False,
    )
    observed_statuses = []

    def fail_reservation(_draft_id):
        current = agent.draft_repo.get_by_id(draft["id"])
        observed_statuses.append(current["status"])
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(agent.draft_repo, "reserve_capture_for_commit", fail_reservation)

    with pytest.raises(RuntimeError, match="simulated write failure"):
        agent.commit_draft(draft["id"])

    restored = agent.draft_repo.get_by_id(draft["id"])
    assert observed_statuses == ["committing"]
    assert restored["status"] == draft["status"]


def test_retry_after_partial_capture_write_reuses_reserved_capture(tmp_path, monkeypatch):
    config = CaptureConfig(
        llm_api_key="sk-test",
        qwen_api_key="",
        vault_path=str(tmp_path / "vault"),
        db_path=str(tmp_path / "captures.db"),
    )
    agent = CaptureAgent(config)
    draft = agent.create_editable_draft(
        "部分写入失败后重试也只能生成一份正式知识文件。",
        title="部分失败恢复",
        organize=False,
    )
    original_write = agent._write_capture_note
    failed_once = False

    def fail_after_raw_write(*args, stage, **kwargs):
        nonlocal failed_once
        if stage == "captures" and not failed_once:
            failed_once = True
            raise RuntimeError("simulated capture note failure")
        return original_write(*args, stage=stage, **kwargs)

    monkeypatch.setattr(agent, "_write_capture_note", fail_after_raw_write)

    with pytest.raises(RuntimeError, match="simulated capture note failure"):
        agent.commit_draft(draft["id"])

    committed = agent.commit_draft(draft["id"])
    capture_count = agent.conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
    raw_files = list((tmp_path / "vault" / "_archive" / "raw").glob("*.md"))
    capture_files = list((tmp_path / "vault" / "captures").glob("*.md"))

    assert committed["status"] == "committed"
    assert capture_count == 1
    assert len(raw_files) == 1
    assert len(capture_files) == 1


def test_stale_capture_commit_claim_can_be_recovered(tmp_path):
    config = CaptureConfig(
        llm_api_key="sk-test",
        qwen_api_key="",
        vault_path=str(tmp_path / "vault"),
        db_path=str(tmp_path / "captures.db"),
    )
    agent = CaptureAgent(config)
    draft = agent.create_editable_draft(
        "进程崩溃留下的旧提交认领应该可以恢复。",
        title="旧认领恢复",
        organize=False,
    )
    agent.conn.execute(
        """UPDATE capture_drafts
           SET status = 'committing', updated_at = '2000-01-01T00:00:00+00:00'
           WHERE id = ?""",
        (draft["id"],),
    )
    agent.conn.commit()

    result = agent.commit_draft(draft["id"])

    assert result["status"] == "committed"
    assert agent.draft_repo.get_by_id(draft["id"])["status"] == "committed"
