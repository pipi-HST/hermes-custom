"""Tests for the RAG data-hygiene/knowledge-card refactor and DeepSeek web tools."""
from __future__ import annotations

import json
import math
import sqlite3
import struct
from pathlib import Path

import pytest

from agents.capture_agent import CaptureConfig
from agents.rag_graph import _theme_groups_for_captures
from db.embedding_repo import CaptureEmbeddingRepo
from db.repo import CaptureRepo
from db.schema import init_db


def _vec(first: float, second: float = 0.0) -> bytes:
    values = [0.0] * 1024
    values[0] = first
    values[1] = second
    return struct.pack("1024f", *values)


def _write_capture(vault: Path, *, capture_id: str, title: str, body: str) -> str:
    rel_path = f"captures/{capture_id}.md"
    path = vault / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
id: {capture_id}
title: {title}
story: {body}
tags:
  - mcp
---

# {title}

{body}
""",
        encoding="utf-8",
    )
    return rel_path


# ---------------------------------------------------------------------------
# Step 1: archived rows must never be retrieved
# ---------------------------------------------------------------------------


def test_capture_embedding_search_excludes_archived_captures(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("cap-live", "captures/live.md"),
    )
    conn.execute(
        "INSERT INTO captures (id, file_path, archived_at) VALUES (?, ?, ?)",
        ("cap-archived", "captures/archived.md", "2026-01-01T00:00:00+00:00"),
    )
    repo = CaptureEmbeddingRepo(conn)
    for capture_id in ("cap-live", "cap-archived"):
        repo.upsert(
            capture_id=capture_id,
            kind="identity",
            index_text=f"{capture_id} content",
            embedding=_vec(1.0),
            source_hash=f"{capture_id}-identity",
            file_path=f"captures/{capture_id}.md",
        )

    rows = repo.search(_vec(1.0), kinds=("identity",), limit=5)

    assert [row["capture_id"] for row in rows] == ["cap-live"]


# ---------------------------------------------------------------------------
# Step 1: rag_graph must query the real pending_questions table
# ---------------------------------------------------------------------------


def test_theme_groups_query_pending_questions(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    conn.execute(
        """INSERT INTO pending_questions
           (id, agent_type, question_text, related_capture_ids, status, expires_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            "pq1",
            "theme",
            "AI Agent 的工程化边界在哪里？",
            json.dumps(["cap1"], ensure_ascii=False),
            "pending",
            "2030-01-01T00:00:00Z",
        ),
    )

    groups = _theme_groups_for_captures(conn, ["cap1"], limit=3)

    assert groups == [
        {
            "id": "pq1",
            "title": "AI Agent 的工程化边界在哪里？",
            "capture_ids": ["cap1"],
            "status": "pending",
        }
    ]


def test_theme_groups_falls_back_to_legacy_theme_questions_table():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE theme_questions (
            id TEXT,
            question_text TEXT,
            related_capture_ids TEXT,
            status TEXT,
            created_at TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO theme_questions VALUES (?, ?, ?, ?, ?)",
        ("tq1", "旧主题问题", json.dumps(["cap1"]), "pending", "2026-01-01T00:00:00Z"),
    )

    groups = _theme_groups_for_captures(conn, ["cap1"], limit=3)

    assert [group["title"] for group in groups] == ["旧主题问题"]


# ---------------------------------------------------------------------------
# Step 2: knowledge cards + literal keyword fallback
# ---------------------------------------------------------------------------


def test_search_garden_cards_recalls_accepted_cards_by_keyword(tmp_path):
    from bridge.rag_bridge import _search_garden_cards

    conn = init_db(str(tmp_path / "captures.db"))
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, payload_json, status, capture_ids)
           VALUES (?, ?, ?, ?, '[]')""",
        (
            "g1",
            "MCP 协议",
            '{"schema_version":"garden_card.v2","claim":"模型上下文协议让工具接入标准化。","card_kind":"viewpoint","source_ids":[]}',
            "accepted",
        ),
    )
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, payload_json, status, capture_ids)
           VALUES (?, ?, ?, ?, '[]')""",
        ("g2", "无关卡片", '{"schema_version":"garden_card.v2","claim":"与查询没有交集。","card_kind":"viewpoint","source_ids":[]}', "accepted"),
    )
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, payload_json, status, capture_ids)
           VALUES (?, ?, ?, ?, '[]')""",
        ("g3", "MCP 协议归档", '{"schema_version":"garden_card.v2","claim":"已归档，不应被召回。","card_kind":"viewpoint","source_ids":[]}', "archived"),
    )

    cards = _search_garden_cards("什么是 MCP 协议？", conn, limit=3)

    assert [card["id"] for card in cards] == ["g1"]
    assert cards[0]["matched_kind"] == "garden_card"
    assert cards[0]["source_kind"] == "garden_card"
    assert cards[0]["title"] == "【知识卡片】MCP 协议"


def test_rag_bridge_source_map_marks_garden_card_as_viewpoint(monkeypatch, tmp_path):
    from bridge import rag_bridge

    db_path = tmp_path / "captures.db"
    vault = tmp_path / "vault"
    rel_path = _write_capture(
        vault,
        capture_id="cap1",
        title="MCP 协议笔记",
        body="MCP 是模型上下文协议，用于标准化工具接入。",
    )
    conn = init_db(str(db_path))
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("cap1", rel_path),
    )
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="identity",
        index_text="MCP 协议 identity",
        embedding=_vec(1.0),
        source_hash="cap1-identity",
        file_path=rel_path,
    )
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, payload_json, status, capture_ids)
           VALUES (?, ?, ?, ?, ?)""",
        (
            "g1",
            "MCP 协议知识卡片",
            '{"schema_version":"garden_card.v2","claim":"MCP 让模型能安全接入外部工具。","card_kind":"viewpoint","source_ids":["cap1"]}',
            "accepted",
            json.dumps(["cap1"]),
        ),
    )

    class FakeEmbedder:
        def __init__(self, **_kwargs):
            pass

        def embed(self, _text: str) -> bytes:
            return _vec(1.0)

    class FakeRagAgent:
        def __init__(self, **_kwargs):
            pass

        def query(self, _question, captures, *, context_pack=None):
            return {
                "answer": "MCP 是模型上下文协议。[1]",
                "sources": [
                    {"title": capture.get("title", ""), "file_path": capture.get("file_path", "")}
                    for capture in captures
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
            llm_base_url="https://api.deepseek.com",
        ),
    )
    monkeypatch.setattr(rag_bridge, "Embedder", FakeEmbedder)
    monkeypatch.setattr(rag_bridge, "RagAgent", FakeRagAgent)
    rag_bridge._config = None
    rag_bridge._repo = None

    envelope = rag_bridge.search_knowledge_base("什么是 MCP 协议？")

    # Evidence now owns the reserved front seats; the card is still present and
    # remains a first-class viewpoint source.
    first = envelope["source_map"][0]
    assert first["type"] in {"capture_note", "capture_section"}
    assert first["role"] == "evidence"
    capture_entries = [
        entry for entry in envelope["source_map"] if entry["type"] != "knowledge_card"
    ]
    card_entries = [
        entry for entry in envelope["source_map"] if entry["type"] == "knowledge_card"
    ]
    assert capture_entries
    assert card_entries
    assert all(entry["role"] == "evidence" for entry in capture_entries)
    assert card_entries[0]["role"] == "viewpoint"
    assert card_entries[0]["title"].startswith("【知识卡片】")
    assert envelope["meta"]["result_count"] >= 2


def test_indexed_search_relevance_gate_keeps_literal_keyword_hits(tmp_path):
    from bridge.rag_bridge import _indexed_search

    db_path = tmp_path / "captures.db"
    conn = init_db(str(db_path))
    rows = {
        "cap-high": ("captures/high.md", "MCP 协议详解"),
        "cap-literal": ("captures/literal.md", "MCP 协议补充材料"),
        "cap-noise": ("captures/noise.md", "完全无关的旧笔记"),
    }
    for capture_id, (file_path, _text) in rows.items():
        conn.execute(
            "INSERT INTO captures (id, file_path) VALUES (?, ?)",
            (capture_id, file_path),
        )
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap-high",
        kind="identity",
        index_text="MCP 协议详解",
        embedding=_vec(1.0),
        source_hash="high",
        file_path=rows["cap-high"][0],
    )
    repo.upsert(
        capture_id="cap-literal",
        kind="identity",
        index_text="MCP 协议补充材料",
        embedding=_vec(0.0, 1.0),
        source_hash="literal",
        file_path=rows["cap-literal"][0],
    )
    repo.upsert(
        capture_id="cap-noise",
        kind="identity",
        index_text="完全无关的旧笔记",
        embedding=_vec(-1.0),
        source_hash="noise",
        file_path=rows["cap-noise"][0],
    )

    class FakeEmbedder:
        def embed(self, _text: str) -> bytes:
            return _vec(1.0)

    results = _indexed_search(
        "MCP 是什么",
        CaptureRepo(conn),
        FakeEmbedder(),
        limit=5,
        query_embedding=_vec(1.0),
    )

    ids = {item["id"] for item in results}
    assert ids == {"cap-high", "cap-literal"}


def test_indexed_search_uses_per_kind_threshold(tmp_path):
    from bridge.rag_bridge import _indexed_search

    conn = init_db(str(tmp_path / "captures.db"))
    for capture_id in ("cap-identity", "cap-section"):
        conn.execute(
            "INSERT INTO captures (id, file_path) VALUES (?, ?)",
            (capture_id, f"captures/{capture_id}.md"),
        )
    repo = CaptureEmbeddingRepo(conn)
    score = 0.44
    second = math.sqrt(1 - score * score)
    repo.upsert(
        capture_id="cap-identity",
        kind="identity",
        index_text="无关标题",
        embedding=_vec(score, second),
        source_hash="identity",
        file_path="captures/cap-identity.md",
    )
    repo.upsert(
        capture_id="cap-section",
        kind="section",
        section_id="s1",
        section_title="无关章节",
        index_text="无关正文",
        embedding=_vec(score, second),
        source_hash="section",
        file_path="captures/cap-section.md",
    )

    class FakeEmbedder:
        def embed(self, _text: str) -> bytes:
            return _vec(1.0, 0.0)

    results = _indexed_search(
        "无关问题",
        CaptureRepo(conn),
        FakeEmbedder(),
        limit=5,
        query_embedding=_vec(1.0, 0.0),
    )

    assert {item["id"] for item in results} == {"cap-identity"}


def test_low_score_bigram_still_fails_gate(tmp_path):
    from bridge.rag_bridge import _indexed_search

    conn = init_db(str(tmp_path / "captures.db"))
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES ('cap-noise', 'captures/noise.md')"
    )
    CaptureEmbeddingRepo(conn).upsert(
        capture_id="cap-noise",
        kind="section",
        section_id="s1",
        section_title="",
        index_text="大模型 传话 效应",
        embedding=_vec(-1.0),
        source_hash="noise",
        file_path="captures/noise.md",
    )

    class FakeEmbedder:
        def embed(self, _text: str) -> bytes:
            return _vec(1.0, 0.0)

    results = _indexed_search(
        "大模型传话筒效应",
        CaptureRepo(conn),
        FakeEmbedder(),
        limit=5,
        query_embedding=_vec(1.0, 0.0),
    )

    assert results == []


# ---------------------------------------------------------------------------
# Step 4: DeepSeek official Search & Fetch APIs
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    calls: list[dict] = []
    payload: dict = {}

    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, url, headers=None, json=None, **_kwargs):
        _FakeClient.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(_FakeClient.payload)


def test_deepseek_web_search_sends_official_schema_and_prioritizes_rerank(monkeypatch):
    from bridge import deepseek_web_tools

    _FakeClient.calls = []
    _FakeClient.payload = {
        "results": [
            {
                "title": "低分结果",
                "url": "https://example.com/low",
                "snippet": "low snippet",
                "rerank_score": 0.4,
                "authority": 9,
            },
            {
                "title": "高分结果",
                "url": "https://example.com/high",
                "snippet": "high snippet",
                "summary": "高相关摘要",
                "rerank_score": 1.8,
                "authority": 2,
            },
        ]
    }
    monkeypatch.setattr(deepseek_web_tools.httpx, "Client", _FakeClient)

    results = deepseek_web_tools.deepseek_web_search(
        "MCP 130秒",
        limit=3,
        ori_question="MCP 的 130 秒是什么？",
        need_content=True,
        api_key="sk-test",
        base_url="https://api.deepseek.com/v1/chat/completions",
    )

    call = _FakeClient.calls[0]
    assert call["url"] == "https://api.deepseek.com/v1/tools/search"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["json"] == {
        "query": "MCP 130秒",
        "count": 3,
        "ori_question": "MCP 的 130 秒是什么？",
        "need_content": True,
    }
    assert results[0]["url"] == "https://example.com/high"
    assert results[0]["summary"] == "高相关摘要"
    assert results[0]["description"] == "高相关摘要"
    assert results[0]["content"] == "高相关摘要"
    assert results[0]["rerank_score"] == 1.8
    assert results[0]["authority"] == 2


def test_deepseek_web_fetch_sends_official_schema(monkeypatch):
    from bridge import deepseek_web_tools

    _FakeClient.calls = []
    _FakeClient.payload = {"title": "示例页面", "content": "页面正文内容"}
    monkeypatch.setattr(deepseek_web_tools.httpx, "Client", _FakeClient)

    result = deepseek_web_tools.deepseek_web_fetch(
        "https://example.com/article",
        api_key="sk-test",
        base_url="https://api.deepseek.com/v1",
    )

    call = _FakeClient.calls[0]
    assert call["url"] == "https://api.deepseek.com/v1/tools/fetch"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["json"] == {"url": "https://example.com/article"}
    assert result == {
        "url": "https://example.com/article",
        "title": "示例页面",
        "content": "页面正文内容",
    }


def test_resolve_deepseek_credentials_normalizes_base_url():
    from bridge.deepseek_web_tools import resolve_deepseek_credentials

    assert resolve_deepseek_credentials(
        "k", "https://api.deepseek.com/v1/chat/completions"
    ) == ("k", "https://api.deepseek.com")
    assert resolve_deepseek_credentials("k", "https://api.deepseek.com/v1/") == (
        "k",
        "https://api.deepseek.com",
    )
    assert resolve_deepseek_credentials("k", "")[1] == "https://api.deepseek.com"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # 疑问壳必须被剥掉，但词内汉字一个都不能动
        ("解释器模式有什么用", ["解释器模式"]),
        ("什么是传话筒", ["传话筒"]),
        ("避坑经验有哪些", ["避坑经验"]),
        ("了解了 MCP 之后应该怎么用", ["mcp"]),
        ("了解 MCP 怎么用", ["mcp"]),
        ("传话筒效应怎么理解", ["传话筒效应"]),
        ("请问一下，MCP 的 130秒 是什么？", ["mcp", "130秒"]),
        # 反过拟合组：含"的/和/了"的实词与成语不能被杀
        ("目的地在哪里", ["目的地"]),
        ("总和是多少", ["总和"]),
        ("成本总和是多少", ["成本总和"]),
        ("君子和而不同", ["君子和而不同"]),
        ("和谐社会", ["和谐社会"]),
        ("和而不同", ["和而不同"]),
        ("的确是重点", ["的确是重点"]),
        ("了如指掌", ["了如指掌"]),
        # 复合名词整体保留，靠 FTS 的 bigram OR 回退去召回
        ("转介绍的目的和边界是什么", ["转介绍的目的和边界"]),
        ("", []),
    ],
)
def test_extract_query_keywords_strips_question_filler(query, expected):
    from bridge.rag_bridge import _extract_query_keywords

    assert _extract_query_keywords(query) == expected


def _fts_corpus(tmp_path):
    """One capture whose body mentions every term but never the full phrase."""
    conn = init_db(str(tmp_path / "captures.db"))
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("cap-zhuan", "captures/zhuan.md"),
    )
    repo = CaptureEmbeddingRepo(conn)
    body = (
        "转介绍是一种获客方式，需要先说清楚它的目的，再讲边界条件。"
        "目的不清晰时，转介绍会变成消耗人情。"
    )
    repo.upsert(
        capture_id="cap-zhuan",
        kind="identity",
        index_text=f"文章：转介绍\n内容：{body}",
        embedding=_vec(1.0),
        source_hash="zhuan-identity",
        file_path="captures/zhuan.md",
    )
    for index, chunk in enumerate(
        ["转介绍的目的", "说清楚它的目的", "再讲边界条件"], start=1
    ):
        repo.upsert(
            capture_id="cap-zhuan",
            kind="section",
            section_id=f"section-{index}",
            section_title=f"第{index}节",
            index_text=f"文章：转介绍\n章节：第{index}节\n内容：{chunk}",
            embedding=_vec(1.0),
            source_hash=f"zhuan-{index}",
            file_path="captures/zhuan.md",
        )
    return conn


def test_fts_keyword_rows_falls_back_to_bigram_or(tmp_path):
    """短语查询 0 命中时必须用 bigram OR 补召回，而不是返回空。"""
    from bridge.rag_bridge import _fts_keyword_rows

    conn = _fts_corpus(tmp_path)
    phrase_only = _fts_keyword_rows(conn, ["转介绍的目的和边界"], limit=6)
    assert phrase_only, "复合问句不能因为短语命中为 0 就丢召回"
    assert all(row["capture_id"] == "cap-zhuan" for row in phrase_only)
    # 命中的 section 行必须带回章节名，供引用面板定位
    assert any(row["section_title"] for row in phrase_only)


def test_fts_keyword_rows_reports_missing_fts_table(tmp_path):
    """没有 FTS5 表时返回 None（触发旧版兜底），语法错则返回 []。"""
    from bridge.rag_bridge import _fts_keyword_rows

    conn = init_db(str(tmp_path / "captures.db"))
    conn.execute("DROP TABLE capture_search_fts")
    assert _fts_keyword_rows(conn, ["转介绍"], limit=3) is None
