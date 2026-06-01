from pathlib import Path

from db.repo import CaptureRepo
from db.schema import init_db


def test_search_knowledge_base_does_not_export_by_default(monkeypatch, tmp_path):
    from bridge import rag_bridge
    from bridge.result_envelope import success_envelope

    conn = init_db(str(tmp_path / "captures.db"))
    repo = CaptureRepo(conn)
    captured = {}

    def fake_operation(query, config, repo, *, export_file=False):
        captured["export_file"] = export_file
        return success_envelope(
            agent="rag",
            intent="search_knowledge_base",
            chat_preview="答案 [1]",
            meta={"query": query, "result_count": 0},
        )

    monkeypatch.setattr(rag_bridge, "_init", lambda: (object(), repo))
    monkeypatch.setattr(rag_bridge, "_search_knowledge_base", fake_operation)

    envelope = rag_bridge.search_knowledge_base("问题")

    assert captured["export_file"] is False
    assert envelope["durable_artifacts"] == []


def test_search_knowledge_base_exports_only_when_requested(monkeypatch, tmp_path):
    from bridge import rag_bridge
    from bridge.result_envelope import artifact_ref, success_envelope

    conn = init_db(str(tmp_path / "captures.db"))
    repo = CaptureRepo(conn)
    captured = {}

    def fake_operation(query, config, repo, *, export_file=False):
        captured["export_file"] = export_file
        artifacts = []
        if export_file:
            artifacts.append(
                artifact_ref(
                    type="rag_answer_report",
                    id="r1",
                    title="RAG 完整回答与引用报告",
                    path=str(tmp_path / "r1.md"),
                    status="created",
                )
            )
        return success_envelope(
            agent="rag",
            intent="search_knowledge_base",
            chat_preview="答案 [1]",
            durable_artifacts=artifacts,
            meta={"query": query, "result_count": 0},
        )

    monkeypatch.setattr(rag_bridge, "_init", lambda: (object(), repo))
    monkeypatch.setattr(rag_bridge, "_search_knowledge_base", fake_operation)

    envelope = rag_bridge.search_knowledge_base("问题", export_file=True)

    assert captured["export_file"] is True
    assert envelope["durable_artifacts"][0]["type"] == "rag_answer_report"


def test_rag_export_file_writes_answer_and_full_citations(tmp_path):
    from bridge import rag_bridge

    export_dir = tmp_path / "exports" / "rag"
    results = [
        {
            "id": "cap1",
            "title": "材料一",
            "file_path": "captures/one.md",
            "section_title": "章节一",
        },
        {
            "id": "cap2",
            "title": "材料二",
            "file_path": "captures/two.md",
            "section_title": "",
        },
    ]
    answer = "这是完整回答 [1] [2]"
    sources = [
        {"title": "材料一", "path": "captures/one.md", "section": "章节一"},
        {"title": "材料二", "path": "captures/two.md", "section": ""},
    ]

    artifact = rag_bridge._rag_report_artifact(
        query="测试问题",
        answer=answer,
        sources=sources,
        results=results,
        export_dir=export_dir,
    )

    path = Path(artifact["path"])
    assert artifact["type"] == "rag_answer_report"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "# RAG 完整回答与引用报告" in text
    assert "## 问题" in text
    assert "测试问题" in text
    assert "## 完整回答" in text
    assert "这是完整回答 [1] [2]" in text
    assert "[1] 材料一 / 章节一" in text
    assert "captures/one.md" in text
    assert "[2] 材料二" in text
    assert "captures/two.md" in text
