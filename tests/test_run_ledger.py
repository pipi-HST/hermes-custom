import json

import pytest

from bridge.ai_cancel import AIOperationCancelled
from bridge.result_envelope import success_envelope
from bridge.run_ledger import run_with_ledger
from db.agent_run_repo import AgentRunRepo
from db.schema import init_db


def test_run_with_ledger_records_success(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))

    result = run_with_ledger(
        conn,
        agent="rag",
        intent="search_knowledge_base",
        trigger="wechat",
        input_ref={"query": "AI"},
        operation=lambda: success_envelope(
            agent="rag",
            intent="search_knowledge_base",
            chat_preview="答案",
            meta={"query": "AI", "answer": "完整答案", "result_count": 0},
        ),
    )

    latest = AgentRunRepo(conn).latest(limit=1)[0]
    assert result["status"] == "success"
    assert latest["agent"] == "rag"
    assert latest["intent"] == "search_knowledge_base"
    assert latest["status"] == "success"
    assert json.loads(latest["input_json"]) == {"query": "AI"}
    assert json.loads(latest["output_json"])["answer"] == "完整答案"
    assert json.loads(latest["evidence_json"])["result_count"] == 0
    assert [event["event_type"] for event in AgentRunRepo(conn).events(latest["id"])] == [
        "started",
        "completed",
    ]


def test_run_with_ledger_records_object_refs(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))

    run_with_ledger(
        conn,
        agent="garden",
        intent="synthesize_garden",
        trigger="wechat",
        input_ref={"capture_ids": ["c1"]},
        operation=lambda: success_envelope(
            agent="garden",
            intent="synthesize_garden",
            chat_preview="已生成草稿",
            durable_artifacts=[
                {
                    "type": "garden_draft",
                    "id": "draft1",
                    "title": "Draft",
                    "status": "draft_ready",
                }
            ],
            meta={"capture_ids": ["c1"], "draft_id": "draft1"},
        ),
    )

    repo = AgentRunRepo(conn)
    latest = repo.latest(limit=1)[0]
    refs = repo.object_refs(latest["id"])
    assert [(row["object_type"], row["object_id"], row["role"]) for row in refs] == [
        ("capture", "c1", "source"),
        ("garden_draft", "draft1", "subject"),
    ]


def test_run_with_ledger_records_error_and_reraises(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))

    def fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_with_ledger(
            conn,
            agent="theme",
            intent="run_theme_scan",
            trigger="wechat",
            input_ref={"rescan_all": True},
            operation=fail,
        )

    latest = AgentRunRepo(conn).latest(limit=1)[0]
    assert latest["status"] == "error"
    assert json.loads(latest["errors_json"]) == ["boom"]
    assert [event["event_type"] for event in AgentRunRepo(conn).events(latest["id"])] == [
        "started",
        "failed",
    ]


def test_run_with_ledger_records_cancelled_and_reraises(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))

    def cancel():
        raise AIOperationCancelled(operation_id="op-1", stage="capture.commit.before_write")

    with pytest.raises(AIOperationCancelled):
        run_with_ledger(
            conn,
            agent="capture",
            intent="commit_capture_draft",
            trigger="dashboard",
            input_ref={"draft_id": "draft-1"},
            operation=cancel,
        )

    repo = AgentRunRepo(conn)
    latest = repo.latest(limit=1)[0]
    evidence = json.loads(latest["evidence_json"])
    assert latest["status"] == "cancelled"
    assert evidence["operation_id"] == "op-1"
    assert evidence["cancelled_stage"] == "capture.commit.before_write"
    assert [event["event_type"] for event in repo.events(latest["id"])] == [
        "started",
        "cancelled",
    ]
