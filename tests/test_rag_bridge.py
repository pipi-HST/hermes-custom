"""Tests for RAG bridge indexed retrieval."""
from __future__ import annotations

import struct
import json
from pathlib import Path

import pytest

from agents.capture_agent import CaptureConfig
from db.embedding_repo import CaptureEmbeddingRepo
from db.schema import init_db


def _vec(first: float, second: float = 0.0) -> bytes:
    values = [0.0] * 1024
    values[0] = first
    values[1] = second
    return struct.pack("1024f", *values)


def _write_capture(vault: Path) -> str:
    rel_path = "captures/long.md"
    path = vault / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
id: cap1
title: 长文
story: 这是一篇长文。
tags:
  - ai
---

# 长文

## 第一部分
这里讨论背景材料。

## 第二部分
这里讨论节奏狙击和 Terminal-Bench 的故意留坑。

## 第三部分
这里讨论后续影响。
""",
        encoding="utf-8",
    )
    return rel_path


def _write_learning_note(vault: Path) -> str:
    rel_path = "learning/基于知识库回答这个问题：最近市场会涨还是会跌-41cc4cc1.md"
    path = vault / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
id: note1
type: learning_note
title: 基于知识库回答这个问题：最近市场会涨还是会跌
story: 市场短期有韧性，中期压力积累。
---

# 基于知识库回答这个问题：最近市场会涨还是会跌

## 解释：基于知识库回答这个问题：最近市场会涨还是会跌

短期市场尚不具备形成历史大顶的典型特征，但中长期估值已进入偏贵区域。

原回答还提到其他背景材料 [2][3]。

## 参考来源

- answer：[1] 【行业知识圈儿.宏观】2026年5月15日 财报分析和二季度市场展望
""",
        encoding="utf-8",
    )
    return rel_path


def test_search_knowledge_base_empty_store_returns_no_knowledge_material(monkeypatch, tmp_path):
    from bridge import rag_bridge

    db_path = tmp_path / "captures.db"
    vault = tmp_path / "vault"
    init_db(str(db_path)).close()

    class FakeEmbedder:
        def __init__(self, **_kwargs):
            pass

        def embed(self, _text: str) -> bytes:
            return _vec(1.0, 0.0)

    monkeypatch.setattr(
        rag_bridge,
        "load_config",
        lambda _path: CaptureConfig(
            vault_path=str(vault),
            db_path=str(db_path),
            qwen_api_key="qwen-test",
            llm_api_key="llm-test",
        ),
    )
    monkeypatch.setattr(rag_bridge, "Embedder", FakeEmbedder)
    rag_bridge._config = None
    rag_bridge._repo = None

    envelope = rag_bridge.search_knowledge_base("空库能回答吗？")

    assert envelope["chat_preview"] == "无知识库资料：当前本地知识库没有可引用的资料。"
    assert envelope["source_map"] == []
    assert envelope["meta"]["result_count"] == 0


def test_search_knowledge_base_uses_best_section_embedding(monkeypatch, tmp_path):
    from bridge import rag_bridge

    db_path = tmp_path / "captures.db"
    vault = tmp_path / "vault"
    rel_path = _write_capture(vault)
    conn = init_db(str(db_path))
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("cap1", rel_path),
    )
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, one_liner, core_arguments, knowledge_links, key_materials,
            my_view, status, capture_ids, created_at, updated_at, review_interval)
           VALUES (?, ?, ?, ?, ?, ?, '', 'seed', ?, ?, ?, '1d')""",
        (
            "g1",
            "AI Agent 工程化",
            "从单次调用走向流程化协作。",
            "[]",
            "[]",
            "[]",
            json.dumps(["cap1"], ensure_ascii=False),
            "2026-05-31T00:00:00+00:00",
            "2026-05-31T00:00:00+00:00",
        ),
    )
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="identity",
        index_text="长文整体身份",
        embedding=_vec(0.1, 1.0),
        source_hash="identity",
        file_path=rel_path,
    )
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="first",
        section_title="第一部分",
        index_text="背景材料",
        embedding=_vec(0.2, 1.0),
        source_hash="first",
        file_path=rel_path,
    )
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="second",
        section_title="第二部分",
        index_text="节奏狙击 Terminal-Bench 故意留坑",
        embedding=_vec(1.0, 0.0),
        source_hash="second",
        file_path=rel_path,
    )

    class FakeEmbedder:
        def __init__(self, **_kwargs):
            pass

        def embed(self, _text: str) -> bytes:
            return _vec(1.0, 0.0)

    class FakeRagAgent:
        seen_captures: list[dict] = []
        seen_context_pack: dict = {}

        def __init__(self, **_kwargs):
            pass

        def query(
            self,
            _question: str,
            captures: list[dict],
            *,
            context_pack: dict | None = None,
        ) -> dict:
            FakeRagAgent.seen_captures = captures
            FakeRagAgent.seen_context_pack = context_pack or {}
            return {
                "answer": "第二部分是重点。[1]",
                "sources": [
                    {
                        "title": captures[0]["title"],
                        "story": captures[0].get("story", ""),
                        "file_path": captures[0]["file_path"],
                        "section_title": captures[0].get("section_title", ""),
                    }
                ],
            }

    monkeypatch.setattr(
        rag_bridge,
        "load_config",
        lambda _path: CaptureConfig(
            vault_path=str(vault),
            db_path=str(db_path),
            qwen_api_key="qwen-test",
            llm_api_key="llm-test",
        ),
    )
    monkeypatch.setattr(rag_bridge, "Embedder", FakeEmbedder)
    monkeypatch.setattr(rag_bridge, "RagAgent", FakeRagAgent)
    rag_bridge._config = None
    rag_bridge._repo = None

    envelope = rag_bridge.search_knowledge_base("Terminal-Bench 的坑是什么？")

    assert envelope["source_map"][0]["ref"] == "1"
    assert envelope["source_map"][0]["type"] == "capture_section"
    assert envelope["source_map"][0]["file_path"] == rel_path
    assert envelope["source_map"][0]["section"] == "第二部分"
    assert "Terminal-Bench" in envelope["source_map"][0]["excerpt"]
    assert FakeRagAgent.seen_captures[0]["section_title"] == "第二部分"
    assert "Terminal-Bench" in FakeRagAgent.seen_captures[0]["body"]
    assert "背景材料" not in FakeRagAgent.seen_captures[0]["body"]
    assert FakeRagAgent.seen_context_pack["garden_count"] == 1
    assert envelope["meta"]["context_pack"]["garden_count"] == 1
    assert "trace" in envelope["meta"]
    assert envelope["meta"]["graph_trace"]["seed_count"] == 1
    assert envelope["meta"]["graph_trace"]["final_count"] >= 1


def test_search_knowledge_base_accepts_explicit_context(monkeypatch, tmp_path):
    from bridge import rag_bridge
    from bridge.context import KnowledgeContext
    from db.repo import CaptureRepo

    db_path = tmp_path / "captures.db"
    vault = tmp_path / "vault"
    rel_path = _write_capture(vault)
    conn = init_db(str(db_path))
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("cap1", rel_path),
    )
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="second",
        section_title="第二部分",
        index_text="节奏狙击 Terminal-Bench 故意留坑",
        embedding=_vec(1.0, 0.0),
        source_hash="second",
        file_path=rel_path,
    )

    class FakeEmbedder:
        def __init__(self, **_kwargs):
            pass

        def embed(self, _text: str) -> bytes:
            return _vec(1.0, 0.0)

    class FakeRagAgent:
        seen_capture_ids: list[str] = []

        def __init__(self, **_kwargs):
            pass

        def query(self, _question, captures, *, context_pack=None):
            FakeRagAgent.seen_capture_ids = [str(item.get("id")) for item in captures]
            return {
                "answer": "显式 context 能完成检索。[1]",
                "sources": [
                    {
                        "title": captures[0]["title"],
                        "file_path": captures[0]["file_path"],
                        "section_title": captures[0].get("section_title", ""),
                    }
                ],
            }

    config = CaptureConfig(
        vault_path=str(vault),
        db_path=str(db_path),
        qwen_api_key="qwen-test",
        llm_api_key="llm-test",
    )
    context = KnowledgeContext(
        config=config,
        conn=conn,
        vault_path=vault,
    )
    monkeypatch.setattr(rag_bridge, "load_config", lambda _path: pytest.fail("context path should not load config"))
    monkeypatch.setattr(rag_bridge, "Embedder", FakeEmbedder)
    monkeypatch.setattr(rag_bridge, "RagAgent", FakeRagAgent)

    envelope = rag_bridge.search_knowledge_base("Terminal-Bench", context=context)

    assert envelope["status"] == "success"
    assert envelope["meta"]["answer"] == "显式 context 能完成检索。[1]"
    assert envelope["source_map"][0]["section"] == "第二部分"
    assert [item["id"] for item in envelope["source_map"]] == FakeRagAgent.seen_capture_ids
    assert CaptureRepo(context.conn).get_by_id("cap1") is not None


def test_search_knowledge_base_warns_when_answer_has_no_citations(monkeypatch, tmp_path):
    from bridge import rag_bridge

    db_path = tmp_path / "captures.db"
    vault = tmp_path / "vault"
    rel_path = _write_capture(vault)
    conn = init_db(str(db_path))
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("cap1", rel_path),
    )
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="second",
        section_title="第二部分",
        index_text="节奏狙击 Terminal-Bench 故意留坑",
        embedding=_vec(1.0, 0.0),
        source_hash="second",
        file_path=rel_path,
    )

    class FakeEmbedder:
        def __init__(self, **_kwargs):
            pass

        def embed(self, _text: str) -> bytes:
            return _vec(1.0, 0.0)

    class FakeRagAgent:
        def __init__(self, **_kwargs):
            pass

        def query(self, _question, captures, *, context_pack=None):
            return {
                "answer": "第二部分是重点，但这个回答没有编号引用。",
                "sources": [
                    {
                        "title": captures[0]["title"],
                        "file_path": captures[0]["file_path"],
                        "section_title": captures[0].get("section_title", ""),
                    }
                ],
            }

    monkeypatch.setattr(
        rag_bridge,
        "load_config",
        lambda _path: CaptureConfig(
            vault_path=str(vault),
            db_path=str(db_path),
            qwen_api_key="qwen-test",
            llm_api_key="llm-test",
        ),
    )
    monkeypatch.setattr(rag_bridge, "Embedder", FakeEmbedder)
    monkeypatch.setattr(rag_bridge, "RagAgent", FakeRagAgent)
    rag_bridge._config = None
    rag_bridge._repo = None

    envelope = rag_bridge.search_knowledge_base("Terminal-Bench 的坑是什么？")

    assert envelope["warnings"] == ["missing_citations"]
    assert envelope["meta"]["citation_validation"] == {
        "valid": False,
        "missing_citations": True,
        "unknown_citation_ids": [],
        "cited_source_ids": [],
    }


def test_card_candidate_pagination_reaches_later_pages(monkeypatch, tmp_path):
    from bridge import rag_bridge

    conn = init_db(str(tmp_path / "cards.db"))
    for index in range(5):
        conn.execute(
            """INSERT INTO garden_cards
               (id, title, one_liner, content_markdown, status, updated_at)
               VALUES (?, ?, ?, ?, 'accepted', ?)""",
            (
                f"card{index}",
                "分页目标" if index == 0 else f"无关卡片{index}",
                "",
                "分页目标正文" if index == 0 else "无关正文",
                f"2026-01-0{index + 1}T00:00:00+00:00",
            ),
        )
    conn.commit()
    monkeypatch.setattr(rag_bridge, "CARD_CANDIDATE_PAGE_SIZE", 2)
    monkeypatch.setattr(rag_bridge, "CARD_CANDIDATE_MAX_SCAN", 10)

    hits = rag_bridge._search_garden_cards("分页目标", conn, limit=1)

    assert [hit["id"] for hit in hits] == ["card0"]


def test_search_knowledge_base_does_not_repair_empty_answer(monkeypatch, tmp_path):
    from bridge import rag_bridge

    db_path = tmp_path / "captures.db"
    vault = tmp_path / "vault"
    rel_path = _write_capture(vault)
    conn = init_db(str(db_path))
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("cap1", rel_path),
    )
    CaptureEmbeddingRepo(conn).upsert(
        capture_id="cap1",
        kind="section",
        section_id="second",
        section_title="第二部分",
        index_text="节奏狙击 Terminal-Bench 故意留坑",
        embedding=_vec(1.0, 0.0),
        source_hash="second",
        file_path=rel_path,
    )

    class FakeEmbedder:
        def __init__(self, **_kwargs):
            pass

        def embed(self, _text: str) -> bytes:
            return _vec(1.0, 0.0)

    class FakeRagAgent:
        repair_calls = 0

        def __init__(self, **_kwargs):
            pass

        def query(self, _question, captures, *, context_pack=None):
            return {
                "answer": "",
                "sources": [
                    {
                        "title": captures[0]["title"],
                        "file_path": captures[0]["file_path"],
                        "section_title": captures[0].get("section_title", ""),
                    }
                ],
            }

        def repair_citations(self, answer_text, source_map):
            FakeRagAgent.repair_calls += 1
            return "这是 repair 凭空造出来的答案。[1]"

    monkeypatch.setattr(
        rag_bridge,
        "load_config",
        lambda _path: CaptureConfig(
            vault_path=str(vault),
            db_path=str(db_path),
            qwen_api_key="qwen-test",
            llm_api_key="llm-test",
        ),
    )
    monkeypatch.setattr(rag_bridge, "Embedder", FakeEmbedder)
    monkeypatch.setattr(rag_bridge, "RagAgent", FakeRagAgent)
    rag_bridge._config = None
    rag_bridge._repo = None

    envelope = rag_bridge.search_knowledge_base("Terminal-Bench 的坑是什么？")

    assert envelope["meta"]["empty_answer"] is True
    assert envelope["meta"]["citation_repaired"] is False
    assert FakeRagAgent.repair_calls == 0
    assert "知识库中没有找到相关信息" in envelope["chat_preview"]
    assert "凭空造出来" not in envelope["chat_preview"]


def test_search_knowledge_base_repairs_missing_citations_once(monkeypatch, tmp_path):
    from bridge import rag_bridge

    db_path = tmp_path / "captures.db"
    vault = tmp_path / "vault"
    rel_path = _write_capture(vault)
    conn = init_db(str(db_path))
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("cap1", rel_path),
    )
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="second",
        section_title="第二部分",
        index_text="节奏狙击 Terminal-Bench 故意留坑",
        embedding=_vec(1.0, 0.0),
        source_hash="second",
        file_path=rel_path,
    )

    class FakeEmbedder:
        def __init__(self, **_kwargs):
            pass

        def embed(self, _text: str) -> bytes:
            return _vec(1.0, 0.0)

    class FakeRagAgent:
        repair_calls = 0

        def __init__(self, **_kwargs):
            pass

        def query(self, _question, captures, *, context_pack=None):
            return {
                "answer": "第二部分是重点，但这个回答缺少编号引用。",
                "sources": [
                    {
                        "title": captures[0]["title"],
                        "file_path": captures[0]["file_path"],
                        "section_title": captures[0].get("section_title", ""),
                    }
                ],
            }

        def repair_citations(self, answer_text, source_map):
            FakeRagAgent.repair_calls += 1
            assert "第二部分是重点" in answer_text
            assert source_map[0]["title"] == "长文"
            return "第二部分是重点，但这个回答已经补上编号引用。[1]"

    monkeypatch.setattr(
        rag_bridge,
        "load_config",
        lambda _path: CaptureConfig(
            vault_path=str(vault),
            db_path=str(db_path),
            qwen_api_key="qwen-test",
            llm_api_key="llm-test",
        ),
    )
    monkeypatch.setattr(rag_bridge, "Embedder", FakeEmbedder)
    monkeypatch.setattr(rag_bridge, "RagAgent", FakeRagAgent)
    rag_bridge._config = None
    rag_bridge._repo = None

    envelope = rag_bridge.search_knowledge_base("Terminal-Bench 的坑是什么？")

    assert FakeRagAgent.repair_calls == 1
    assert envelope["warnings"] == []
    assert envelope["meta"]["answer"] == "第二部分是重点，但这个回答已经补上编号引用。[1]"
    assert envelope["meta"]["citation_validation"]["valid"] is True
    assert "已经补上编号引用。[1]" in envelope["chat_preview"]


def test_indexed_search_ignores_managed_section_embeddings(tmp_path):
    from bridge import rag_bridge
    from db.repo import CaptureRepo

    db_path = tmp_path / "captures.db"
    vault = tmp_path / "vault"
    rel_path = _write_capture(vault)
    conn = init_db(str(db_path))
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("cap1", rel_path),
    )
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="identity",
        index_text="AI Agent 工程化整体身份",
        embedding=_vec(0.7, 0.0),
        source_hash="identity",
        file_path=rel_path,
    )
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="related",
        section_title="相关笔记",
        index_text="Building Effective AI Agents Opus 4.8 Claude Code",
        embedding=_vec(1.0, 0.0),
        source_hash="related",
        file_path=rel_path,
    )
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="second",
        section_title="第二部分",
        index_text="节奏狙击 Terminal-Bench 故意留坑",
        embedding=_vec(0.9, 0.0),
        source_hash="second",
        file_path=rel_path,
    )

    class FakeEmbedder:
        def embed(self, _text: str) -> bytes:
            return _vec(1.0, 0.0)

    results = rag_bridge._indexed_search(
        "AI Agent 工程化",
        CaptureRepo(conn),
        FakeEmbedder(),
        limit=1,
    )

    assert len(results) == 1
    assert results[0]["section_title"] == "第二部分"
    assert results[0]["matched_kind"] == "section"


def test_search_knowledge_base_graph_expands_to_related_capture(monkeypatch, tmp_path):
    from bridge import rag_bridge

    db_path = tmp_path / "captures.db"
    vault = tmp_path / "vault"
    rel_path = _write_capture(vault)
    second_path = "captures/related.md"
    second_note = vault / second_path
    second_note.parent.mkdir(parents=True, exist_ok=True)
    second_note.write_text(
        """---
id: cap2
title: 相关材料
story: 工作流材料。
---

## 工作流模式
这里补充 workflow router evaluator optimizer。
""",
        encoding="utf-8",
    )
    conn = init_db(str(db_path))
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("cap1", rel_path),
    )
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("cap2", second_path),
    )
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, one_liner, core_arguments, knowledge_links, key_materials,
            my_view, status, capture_ids, created_at, updated_at, review_interval)
           VALUES (?, ?, ?, ?, ?, ?, '', 'seed', ?, ?, ?, '1d')""",
        (
            "g1",
            "AI Agent 工程化",
            "工程化来自工具、流程和护栏。",
            "[]",
            "[]",
            "[]",
            json.dumps(["cap1", "cap2"], ensure_ascii=False),
            "2026-05-31T00:00:00+00:00",
            "2026-05-31T00:00:00+00:00",
        ),
    )
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="second",
        section_title="第二部分",
        index_text="节奏狙击 Terminal-Bench",
        embedding=_vec(1.0, 0.0),
        source_hash="second",
        file_path=rel_path,
    )
    repo.upsert(
        capture_id="cap2",
        kind="section",
        section_id="workflow",
        section_title="工作流模式",
        index_text="workflow router evaluator optimizer",
        embedding=_vec(0.95, 0.0),
        source_hash="workflow",
        file_path=second_path,
    )

    class FakeEmbedder:
        def __init__(self, **_kwargs):
            pass

        def embed(self, _text: str) -> bytes:
            return _vec(1.0, 0.0)

    class FakeRagAgent:
        seen_captures: list[dict] = []

        def __init__(self, **_kwargs):
            pass

        def query(self, _question, captures, *, context_pack=None):
            FakeRagAgent.seen_captures = captures
            return {
                "answer": "回答。[1][2]",
                "sources": [
                    {
                        "title": item["title"],
                        "file_path": item["file_path"],
                        "section_title": item.get("section_title", ""),
                    }
                    for item in captures
                ],
            }

    monkeypatch.setattr(
        rag_bridge,
        "load_config",
        lambda _path: CaptureConfig(
            vault_path=str(vault),
            db_path=str(db_path),
            qwen_api_key="qwen-test",
            llm_api_key="llm-test",
        ),
    )
    monkeypatch.setattr(rag_bridge, "Embedder", FakeEmbedder)
    monkeypatch.setattr(rag_bridge, "RagAgent", FakeRagAgent)
    rag_bridge._config = None
    rag_bridge._repo = None

    envelope = rag_bridge.search_knowledge_base("Terminal-Bench")

    assert envelope["meta"]["graph_trace"]["garden_count"] == 1
    assert envelope["meta"]["graph_trace"]["expanded_capture_count"] == 2
    assert [item["id"] for item in FakeRagAgent.seen_captures] == ["cap1", "cap2"]
