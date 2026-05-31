import json

from bridge.agent_run_bridge import get_agent_run_status
from db.agent_run_repo import AgentRunRepo
from db.schema import init_db


def test_get_agent_run_status_formats_recent_runs(monkeypatch, tmp_path):
    from bridge import agent_run_bridge

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
            "sources": [
                {
                    "id": "cap1",
                    "title": "Building Effective AI Agents",
                    "path": "captures/Building Effective AI Agents.md",
                    "section": "构建大语言模型代理的经验与方法",
                    "type": "capture_section",
                }
            ],
        },
        evidence={
            "result_count": 2,
            "source_count": 1,
            "source_titles": ["Building Effective AI Agents"],
        },
        warnings=[],
    )

    monkeypatch.setattr(
        agent_run_bridge,
        "load_config",
        lambda _path: type("Config", (), {"db_path": str(db_path)})(),
    )

    envelope = get_agent_run_status(limit=3)

    assert envelope["agent"] == "system"
    assert envelope["intent"] == "agent_run_status"
    assert "search_knowledge_base" in envelope["chat_preview"]
    assert "result_count=2" in envelope["chat_preview"]
    assert "Building Effective AI Agents" in envelope["chat_preview"]
    assert "构建大语言模型代理的经验与方法" in envelope["chat_preview"]
    assert envelope["meta"]["run_count"] == 1
    assert json.loads(envelope["meta"]["runs"][0]["evidence_json"])["source_count"] == 1
