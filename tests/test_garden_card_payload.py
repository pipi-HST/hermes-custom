from __future__ import annotations

import json

from db.schema import init_db


def test_garden_tables_include_v2_payload_columns(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))

    for table in ("garden_cards", "garden_drafts"):
        columns = {
            row[1]: row for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        assert "schema_version" in columns
        assert "payload_json" in columns
        assert columns["schema_version"][4] == "'garden_card.v1'"
        assert columns["payload_json"][4] == "'{}'"


def test_legacy_row_normalizes_to_v2_payload():
    from agents.garden_card_payload import normalize_garden_payload

    payload = normalize_garden_payload(
        {},
        legacy={
            "title": "治理先行",
            "one_liner": "企业级取数 Agent 应先治理数据口径。",
            "card_kind": "method",
            "core_arguments": json.dumps([
                {"claim": "先定义官方标准数据集。", "source_ids": ["cap1"]},
                {"claim": "再封装分析流程。", "source_ids": ["cap2"]},
            ], ensure_ascii=False),
            "key_materials": json.dumps([
                {"content": "官方口径能减少歧义。", "capture_id": "cap1"},
            ], ensure_ascii=False),
            "knowledge_links": json.dumps(["card2"], ensure_ascii=False),
            "my_view": "- 评审问数 Agent 架构时使用。",
            "capture_ids": json.dumps(["cap1", "cap2"], ensure_ascii=False),
        },
    )

    assert payload["schema_version"] == "garden_card.v2"
    assert payload["title"] == "治理先行"
    assert payload["claim"] == "企业级取数 Agent 应先治理数据口径。"
    assert payload["card_kind"] == "method"
    assert payload["when_to_use"] == ["评审问数 Agent 架构时使用。"]
    assert [item["statement"] for item in payload["core_judgments"]] == [
        "先定义官方标准数据集。",
        "再封装分析流程。",
    ]
    assert payload["core_judgments"][0]["support"][0]["summary"] == "官方口径能减少歧义。"
    assert payload["related_card_ids"] == ["card2"]
    assert payload["source_ids"] == ["cap1", "cap2"]


def test_render_v2_markdown_separates_judgment_reasoning_boundaries_and_sources():
    from agents.garden_card_payload import render_garden_card_markdown

    payload = {
        "schema_version": "garden_card.v2",
        "title": "治理先行",
        "card_kind": "method",
        "claim": "企业级取数 Agent 应先治理数据口径。",
        "confidence": "medium",
        "when_to_use": ["评审问数 Agent 架构时使用。"],
        "core_judgments": [
            {
                "id": "j1",
                "statement": "先定义官方标准数据集。",
                "reasoning": "否则 Agent 会在相似表之间自信选错。",
                "support": [
                    {"summary": "官方口径能减少歧义。", "source_ids": ["cap1"]},
                ],
            }
        ],
        "boundaries": ["不适用于口径已经完全稳定的小型一次性分析。"],
        "action_items": ["先列出 5 个高频指标和唯一计算口径。"],
        "related_card_ids": ["card2"],
        "source_ids": ["cap1"],
    }

    markdown = render_garden_card_markdown(
        payload,
        status="seed",
        created_at="2026-06-18T00:00:00+00:00",
        updated_at="2026-06-18T00:00:00+00:00",
        source_links=["[[captures/取数Agent|取数Agent]]"],
        related_links=["[[garden/相关卡|相关卡]]"],
    )

    assert "schema_version: garden_card.v2" in markdown
    assert "> 企业级取数 Agent 应先治理数据口径。" in markdown
    assert "## 什么时候用" in markdown
    assert "## 核心判断" in markdown
    assert "## 为什么成立" in markdown
    assert "### 1. 先定义官方标准数据集。" in markdown
    assert "- 理由：否则 Agent 会在相似表之间自信选错。" in markdown
    assert "- 依据：官方口径能减少歧义。" in markdown
    assert "## 边界与反例" in markdown
    assert "## 行动启发" in markdown
    assert "## 来源" in markdown
    assert "## 内容" not in markdown
    assert "[quote]" not in markdown
    assert "[case]" not in markdown
    assert "[data]" not in markdown

