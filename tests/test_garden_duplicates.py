from __future__ import annotations

import json

from agents.garden_duplicates import find_garden_duplicate
from db.schema import init_db


def _payload(title: str, claim: str, card_kind: str, source_ids: list[str]) -> str:
    return json.dumps(
        {
            "schema_version": "garden_card.v2",
            "title": title,
            "claim": claim,
            "card_kind": card_kind,
            "source_ids": source_ids,
        },
        ensure_ascii=False,
    )


def _insert_card(conn, card_id, title, claim, card_kind, source_ids):
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, card_kind, payload_json, status, capture_ids)
           VALUES (?, ?, ?, ?, 'seed', ?)""",
        (
            card_id,
            title,
            card_kind,
            _payload(title, claim, card_kind, source_ids),
            json.dumps(source_ids, ensure_ascii=False),
        ),
    )


def _insert_draft(conn, draft_id, title, claim, card_kind, source_ids):
    conn.execute(
        """INSERT INTO garden_drafts
           (id, title, card_kind, payload_json, markdown, capture_ids, status)
           VALUES (?, ?, ?, ?, ?, ?, 'draft_ready')""",
        (
            draft_id,
            title,
            card_kind,
            _payload(title, claim, card_kind, source_ids),
            f"# {title}\n",
            json.dumps(source_ids, ensure_ascii=False),
        ),
    )


def test_find_garden_duplicate_blocks_same_one_liner_and_sources(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    _insert_card(
        conn,
        "card1",
        "AI Agent 工程化",
        "AI Agent 工程化的关键是用流程和护栏管理不可靠模型。",
        "method",
        ["cap1", "cap2"],
    )

    result = find_garden_duplicate(
        conn,
        {
            "title": "工程化 Agent",
            "one_liner": "AI Agent 工程化的关键是用流程和护栏管理不可靠模型。",
            "card_kind": "method",
            "core_arguments": [{"claim": "需要流程、工具和质量门禁。"}],
            "capture_ids": ["cap1", "cap2"],
        },
    )

    assert result["status"] == "duplicate"
    assert result["match"]["id"] == "card1"
    assert result["match"]["type"] == "garden_card"
    assert "核心判断几乎相同" in result["reasons"]


def test_find_garden_duplicate_warns_when_sources_overlap(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    _insert_card(
        conn,
        "card1",
        "AI 工作流落地",
        "AI 落地需要嵌入业务流程，而不是只依赖模型能力。",
        "method",
        ["cap1", "cap2", "cap3"],
    )

    result = find_garden_duplicate(
        conn,
        {
            "title": "AI Agent 工具链",
            "one_liner": "AI Agent 要通过工具链进入真实工作流。",
            "card_kind": "method",
            "core_arguments": [{"claim": "工具链决定落地效果。"}],
            "capture_ids": ["cap1", "cap2", "cap4"],
        },
    )

    assert result["status"] == "possible_duplicate"
    assert result["match"]["id"] == "card1"
    assert "来源材料高度重叠" in result["reasons"]


def test_find_garden_duplicate_checks_open_drafts(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    _insert_draft(
        conn,
        "draft1",
        "待确认观点",
        "同一个开放草稿不应该重复生成。",
        "viewpoint",
        ["cap1"],
    )

    result = find_garden_duplicate(
        conn,
        {
            "title": "重复草稿",
            "one_liner": "同一个开放草稿不应该重复生成。",
            "card_kind": "viewpoint",
            "core_arguments": [],
            "capture_ids": ["cap1"],
        },
    )

    assert result["status"] == "duplicate"
    assert result["match"]["type"] == "garden_draft"
    assert result["match"]["id"] == "draft1"


def test_find_garden_duplicate_ignores_unrelated_cards(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    _insert_card(
        conn,
        "card1",
        "无关事实",
        "这是一张完全不同的事实卡。",
        "fact",
        ["cap9"],
    )

    result = find_garden_duplicate(
        conn,
        {
            "title": "AI 方法",
            "one_liner": "AI Agent 需要被工作流约束。",
            "card_kind": "method",
            "core_arguments": [{"claim": "工具和门禁很重要。"}],
            "capture_ids": ["cap1"],
        },
    )

    assert result["status"] == "none"
