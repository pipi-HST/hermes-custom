"""Batch 2 — 长文切分、引用血缘、引用事件与预算控制。"""
from __future__ import annotations

import re
import sqlite3
import struct

import pytest

from agents.capture_indexer import build_section_indexes
from agents.embedder import DIM
from db.schema import init_db


def _long_section_body(chars_per_para: int = 700, paras: int = 4, tail: str = "尾部专属结论XYZ") -> str:
    block = "\n\n".join("甲" * chars_per_para for _ in range(paras))
    return f"## 长章节\n\n{block}\n\n{tail}\n"


def test_long_section_is_split_and_tail_survives():
    items = build_section_indexes("cap1", "captures/long.md", {"title": "长文"}, _long_section_body())
    section_ids = [item.section_id for item in items]
    assert section_ids[0] == "section-1"
    assert "section-1-p2" in section_ids, f"长章节必须滑动窗口切分，实际={section_ids}"
    assert any("尾部专属结论XYZ" in item.index_text for item in items), "文末结论不能被截断丢弃"


def test_headingless_long_body_gets_paragraph_sections():
    body = "\n\n".join("乙" * 300 for _ in range(6))  # 1800 字，无 ##/### 标题
    items = build_section_indexes("cap2", "captures/plain.md", {"title": "无标题长文"}, body)
    sections = [item for item in items if item.kind == "section"]
    assert len(sections) >= 2, f"无标题长文必须按自然段分块，实际={len(sections)}"


def test_short_section_is_not_split():
    body = "## 短章节\n\n只有一句话。\n"
    items = build_section_indexes("cap3", "captures/short.md", {"title": "短文"}, body)
    assert [item.section_id for item in items] == ["section-1"]


# ---------------------------------------------------------------------------
# 链路②③：对话转卡片时的引用继承
# ---------------------------------------------------------------------------
def test_reading_card_inherits_section_capture_and_linked_cards():
    from bridge.reading_bridge import (
        _reading_card_capture_ids,
        _reading_card_linked_card_ids,
    )

    session = {"source_type": "capture_section", "source_id": "cap_1#section-2"}
    evidence = [
        {"source_kind": "capture_section", "source_id": "cap_2#section-1"},
        {"source_kind": "capture_note", "source_id": "cap_3"},
        {"source_kind": "knowledge_card", "source_id": "card_old"},
        {"source_kind": "garden_card", "source_id": "card_old2"},
        {"source_kind": "web", "source_id": "https://example.com"},
    ]

    assert _reading_card_capture_ids(session, evidence) == ["cap_1", "cap_2", "cap_3"]
    assert _reading_card_linked_card_ids(evidence) == ["card_old", "card_old2"]


# ---------------------------------------------------------------------------
# 链路④：只有真正出现在回答正文里的 [n] 才计入引用
# ---------------------------------------------------------------------------
def test_citation_events_only_for_cited_cards(tmp_path):
    from bridge.rag_bridge import record_card_citations

    conn = init_db(str(tmp_path / "cite.db"))
    try:
        conn.execute("INSERT INTO garden_cards (id, title) VALUES ('card_1', 'A')")
        conn.execute("INSERT INTO garden_cards (id, title) VALUES ('card_2', 'B')")
        source_map = [
            {"ref": "1", "type": "knowledge_card", "id": "card_1"},
            {"ref": "2", "type": "knowledge_card", "id": "card_2"},
            {"ref": "3", "type": "capture_section", "id": "cap_9"},
        ]

        recorded = record_card_citations(
            conn, "结论[1]。补充 [3]。", source_map, query="q", run_id="run-1"
        )

        assert recorded == ["card_1"]
        rows = conn.execute("SELECT card_id FROM citation_events").fetchall()
        assert [row["card_id"] for row in rows] == ["card_1"]
        counted = {
            row["id"]: row["citation_count"]
            for row in conn.execute("SELECT id, citation_count FROM garden_cards")
        }
        assert counted == {"card_1": 1, "card_2": 0}

        # 幂等/重复：同一回答再记一次会再加一，但不会给未引用卡片记账
        record_card_citations(conn, "结论[1]", source_map, query="q", run_id="run-2")
        assert conn.execute("SELECT count(*) FROM citation_events").fetchone()[0] == 2
    finally:
        conn.close()


def test_citation_event_and_counter_roll_back_together(tmp_path):
    from bridge.rag_bridge import record_card_citations

    conn = init_db(str(tmp_path / "cite-atomic.db"))
    try:
        conn.execute("INSERT INTO garden_cards (id, title) VALUES ('card_1', 'A')")
        conn.execute("INSERT INTO garden_cards (id, title) VALUES ('card_2', 'B')")
        conn.execute(
            """CREATE TRIGGER fail_second_card
               BEFORE UPDATE ON garden_cards
               WHEN NEW.id = 'card_2'
               BEGIN
                   SELECT RAISE(ABORT, 'boom');
               END"""
        )
        source_map = [
            {"ref": "1", "type": "knowledge_card", "id": "card_1"},
            {"ref": "2", "type": "knowledge_card", "id": "card_2"},
        ]

        with pytest.raises(sqlite3.IntegrityError):
            record_card_citations(
                conn, "结论[1][2]", source_map, query="q", run_id="run-atomic"
            )

        assert conn.execute("SELECT COUNT(*) FROM citation_events").fetchone()[0] == 0
        counts = {
            row["id"]: row["citation_count"]
            for row in conn.execute("SELECT id, citation_count FROM garden_cards")
        }
        assert counts == {"card_1": 0, "card_2": 0}
    finally:
        conn.close()


def test_answer_sources_recognise_knowledge_cards():
    from bridge.answer_bridge import _group_answer_sources

    groups = _group_answer_sources(
        [
            {"type": "knowledge_card", "id": "card_1"},
            {"type": "garden_card", "id": "card_2"},
            {"type": "web", "url": "https://example.com"},
        ]
    )
    assert len(groups["knowledge_sources"]) == 2
    assert groups["source_type"] == "knowledge_web_research"


# ---------------------------------------------------------------------------
# 图检索：只走卡片桥，不再沿主题组无脑扩图
# ---------------------------------------------------------------------------
def test_graph_expansion_ignores_theme_groups():
    from agents.rag_graph import _expanded_capture_ids

    expanded = _expanded_capture_ids(
        ["cap_seed"],
        [{"capture_ids": ["cap_from_card"]}],
        [{"capture_ids": ["cap_from_theme"]}],
        10,
    )
    assert "cap_from_card" in expanded
    assert "cap_from_theme" not in expanded


# ---------------------------------------------------------------------------
# Context 预算与去重
# ---------------------------------------------------------------------------
def test_prompt_budget_and_same_capture_dedupe():
    from agents.rag_agent import (
        MAX_TOTAL_PROMPT_CHARS,
        build_rag_prompt,
        render_prompt,
        select_prompt_sources,
    )

    big = "丙" * 4000
    captures = [
        {"id": "cap_1", "matched_kind": "identity", "title": "整篇", "body": big},
        {"id": "cap_1", "matched_kind": "section", "title": "整篇", "section_title": "第 1 节", "body": big},
        {"id": "cap_2", "matched_kind": "section", "title": "另一篇", "body": big},
    ]
    prompt = build_rag_prompt("问题", captures)

    assert prompt.count('<source id="cap_1"') == 0  # source 标签用编号，不写 capture_id
    assert prompt.count("<source") == 2, "同一篇资料的 identity 与 section 必须去重"
    assert len(prompt) <= MAX_TOTAL_PROMPT_CHARS + 200

    # 编号只有一个来源：同一份 selected 既渲染 prompt 又生成 source_map。
    selected = select_prompt_sources(captures, question="问题")
    rendered = render_prompt("问题", selected)
    assert rendered == prompt
    source_ids = re.findall(r'<source id="(\d+)"', rendered)
    assert len(source_ids) == len(selected) == 2
    assert [item["id"] for item in selected] == ["cap_1", "cap_2"]


def test_prompt_budget_never_drops_a_numbered_source():
    """预算裁剪只能发生在编号之前：被丢掉的来源不能既编号又渲染。"""
    from agents.rag_agent import (
        MAX_TOTAL_PROMPT_CHARS,
        render_prompt,
        select_prompt_sources,
    )

    captures = [
        {"id": f"cap_{index}", "matched_kind": "identity",
         "title": f"第{index}篇", "body": "丁" * 3200}
        for index in range(7)
    ]
    selected = select_prompt_sources(captures, question="问题")
    prompt = render_prompt("问题", selected)
    assert 0 < len(selected) < len(captures), "七篇 3200 字必须触发预算裁剪"
    assert len(prompt) <= MAX_TOTAL_PROMPT_CHARS + 200
    assert len(re.findall(r'<source id="(\d+)"', prompt)) == len(selected)


# ---------------------------------------------------------------------------
# 卡片类别中英文同义词
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("避坑", "risk"),
        ("风险 / 边界", "risk"),
        ("洞察 / 金句", "viewpoint"),
        ("方法 / 框架", "method"),
        ("新知 / 概念", "fact"),
        ("caution", "risk"),
        ("method", "method"),
    ],
)
def test_card_kind_accepts_chinese_labels(raw, expected):
    from agents.garden_card_payload import _normalize_card_kind

    assert _normalize_card_kind(raw) == expected


def test_graph_refresh_builds_card_to_capture_edge(tmp_path):
    """链路⑤：写卡片后自动重建 card_uses_capture 边。"""
    from agents.knowledge_graph import refresh_graph_after_write

    vault = tmp_path / "vault"
    (vault / "captures").mkdir(parents=True)
    (vault / "captures" / "x.md").write_text(
        "---\ntitle: 一篇资料\n---\n\n正文内容\n", encoding="utf-8"
    )
    conn = init_db(str(tmp_path / "graph.db"))
    try:
        conn.execute(
            "INSERT INTO captures (id, file_path) VALUES ('cap_x', 'captures/x.md')"
        )
        conn.execute(
            "INSERT INTO garden_cards (id, title, payload_json, capture_ids, status) "
            "VALUES ('card_x', '卡片', '{\"schema_version\":\"garden_card.v2\",\"claim\":\"一句话\",\"card_kind\":\"viewpoint\"}', '[\"cap_x\"]', 'accepted')"
        )
        conn.commit()

        refresh_graph_after_write(conn, str(vault))

        rows = conn.execute(
            "SELECT relation_type, source_id, target_id FROM knowledge_edges"
        ).fetchall()
        edges = {(row["relation_type"], row["source_id"], row["target_id"]) for row in rows}
        assert ("card_uses_capture", "card_x", "cap_x") in edges
    finally:
        conn.close()


def test_capture_folder_mirrors_frontmatter_and_card_inherits_theme(tmp_path):
    """链路①：frontmatter 专题 → capture_metadata → 卡片 source_theme。"""
    from agents.garden_agent import create_garden_card
    from db.metadata_repo import CaptureMetadataRepo
    from db.repo import CaptureRepo

    vault = tmp_path / "vault"
    (vault / "captures").mkdir(parents=True)
    (vault / "captures" / "a.md").write_text(
        "---\ntitle: 资料A\nfolder_path: AI 产品面试\n---\n\n正文\n", encoding="utf-8"
    )
    conn = init_db(str(tmp_path / "kb.db"))
    try:
        repo = CaptureRepo(conn)
        capture = repo.insert(file_path="captures/a.md")

        # 写入侧：索引时镜像 frontmatter 的 folder_path
        from agents.library_agent import LibraryAgent

        agent = LibraryAgent(vault, outliner=None, db_conn=conn, embedder=None)
        agent._maintain_embedding_index_rows(
            [{"id": capture["id"], "file_path": "captures/a.md"}], apply=True
        )
        assert CaptureMetadataRepo(conn).get(capture["id"])["folder_path"] == "AI 产品面试"

        # 读侧：从资料提炼卡片时继承专题
        card_id = create_garden_card(
            repo,
            {
                "title": "卡片",
                "one_liner": "一句话",
                "card_kind": "fact",
                "capture_ids": [capture["id"]],
            },
        )
        row = conn.execute(
            "SELECT source_theme FROM garden_cards WHERE id = ?", (card_id,)
        ).fetchone()
        assert row["source_theme"] == "AI 产品面试"
    finally:
        conn.close()
