import json

import pytest

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
            meta={"query": "AI", "result_count": 0},
        ),
    )

    latest = AgentRunRepo(conn).latest(limit=1)[0]
    assert result["status"] == "success"
    assert latest["agent"] == "rag"
    assert latest["intent"] == "search_knowledge_base"
    assert latest["status"] == "success"
    assert json.loads(latest["input_json"]) == {"query": "AI"}
    assert json.loads(latest["evidence_json"])["result_count"] == 0


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
