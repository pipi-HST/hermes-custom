import json
from pathlib import Path

from db.agent_run_repo import AgentRunRepo
from db.schema import init_db


def _config(db_path):
    return type("Config", (), {"db_path": str(db_path)})()


def _patch_config(monkeypatch, module, db_path):
    monkeypatch.setattr(module, "_config", None)
    monkeypatch.setattr(module, "_repo", None)
    monkeypatch.setattr(module, "load_config", lambda _path: _config(db_path))


def test_continue_latest_rag_answer_returns_second_page(monkeypatch, tmp_path):
    from bridge import rag_continue_bridge

    db_path = tmp_path / "captures.db"
    conn = init_db(str(db_path))
    repo = AgentRunRepo(conn)
    run = repo.start_run(
        agent="rag",
        intent="search_knowledge_base",
        trigger="wechat",
        input_ref={"query": "AI Agent 工程化"},
    )
    repo.complete_run(
        run["id"],
        output_ref={
            "query": "AI Agent 工程化",
            "answer": "第一段。" * 350 + "\n\n第二页内容。",
            "sources": [{"title": "来源一", "section": "章节一", "path": "captures/one.md"}],
        },
        evidence={"source_count": 1},
        warnings=[],
    )
    _patch_config(monkeypatch, rag_continue_bridge, db_path)

    envelope = rag_continue_bridge.continue_latest_rag_answer()

    assert envelope["agent"] == "rag"
    assert envelope["intent"] == "continue_rag_answer"
    assert "第二页内容" in envelope["chat_preview"]
    assert "📎 来源" in envelope["chat_preview"]
    assert "[1] 来源一 / 章节一" in envelope["chat_preview"]
    assert envelope["meta"]["source_run_id"] == run["id"]
    assert envelope["meta"]["page"] == 2
    assert envelope["source_map"][0]["ref"] == "1"
    assert envelope["source_map"][0]["file_path"] == "captures/one.md"
    assert envelope["source_map"][0]["excerpt"] == ""


def test_continue_latest_rag_answer_advances_after_prior_continue(monkeypatch, tmp_path):
    from bridge import rag_continue_bridge

    db_path = tmp_path / "captures.db"
    conn = init_db(str(db_path))
    repo = AgentRunRepo(conn)
    run = repo.start_run(
        agent="rag",
        intent="search_knowledge_base",
        trigger="wechat",
        input_ref={"query": "AI"},
    )
    repo.complete_run(
        run["id"],
        output_ref={
            "query": "AI",
            "answer": "甲" * 2500 + "第三页",
            "sources": [],
        },
        evidence={},
        warnings=[],
    )
    cont = repo.start_run(
        agent="rag",
        intent="continue_rag_answer",
        trigger="wechat",
        input_ref={"source_run_id": run["id"], "page": 2},
    )
    repo.complete_run(
        cont["id"],
        output_ref={"source_run_id": run["id"], "page": 2},
        evidence={},
        warnings=[],
    )
    _patch_config(monkeypatch, rag_continue_bridge, db_path)

    envelope = rag_continue_bridge.continue_latest_rag_answer()

    assert envelope["meta"]["page"] == 3


def test_continue_latest_rag_answer_handles_missing_answer(monkeypatch, tmp_path):
    from bridge import rag_continue_bridge

    db_path = tmp_path / "captures.db"
    init_db(str(db_path))
    _patch_config(monkeypatch, rag_continue_bridge, db_path)

    envelope = rag_continue_bridge.continue_latest_rag_answer()

    assert envelope["status"] == "error"
    assert "没有找到可继续展开的 RAG 回答" in envelope["chat_preview"]


def test_continue_latest_rag_answer_exports_latest_full_answer(monkeypatch, tmp_path):
    from bridge import rag_continue_bridge

    db_path = tmp_path / "captures.db"
    conn = init_db(str(db_path))
    repo = AgentRunRepo(conn)
    run = repo.start_run(
        agent="rag",
        intent="search_knowledge_base",
        trigger="wechat",
        input_ref={"query": "AI"},
    )
    repo.complete_run(
        run["id"],
        output_ref={
            "query": "AI",
            "answer": "完整回答 [1]",
            "sources": [{"title": "来源一", "section": "章节一", "path": "captures/one.md"}],
        },
        evidence={"source_count": 1},
        warnings=[],
    )
    export_dir = tmp_path / "exports"
    monkeypatch.setattr(
        rag_continue_bridge,
        "_rag_report_artifact",
        lambda **kwargs: {
            "type": "rag_answer_report",
            "id": "r1",
            "title": "RAG 完整回答与引用报告",
            "path": str(export_dir / "r1.md"),
            "status": "created",
        },
    )
    _patch_config(monkeypatch, rag_continue_bridge, db_path)

    envelope = rag_continue_bridge.continue_latest_rag_answer(export_file=True)

    assert "已生成 RAG 完整回答与引用报告文件" in envelope["chat_preview"]
    assert envelope["durable_artifacts"][0]["type"] == "rag_answer_report"
    assert envelope["meta"]["source_run_id"] == run["id"]
