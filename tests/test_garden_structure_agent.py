from __future__ import annotations

import json
from unittest.mock import patch

from agents.garden_structure_agent import GardenStructureAgent


def test_plan_returns_structured_cluster_from_valid_llm_json():
    raw = json.dumps({
        "status": "structured",
        "core_claim": "AI Agent 的价值来自工程底座。",
        "groups": [
            {
                "role": "support",
                "title": "支撑观点",
                "card_ids": ["card1", "card2"],
                "reason": "两张卡共同说明瓶颈不在模型。",
            },
            {
                "role": "method",
                "title": "落地方法",
                "card_ids": ["card3"],
                "reason": "这张卡给出工程化做法。",
            },
        ],
        "evidence_ids": ["cap1", "cap2"],
    }, ensure_ascii=False)

    with patch.object(GardenStructureAgent, "_call_llm", return_value=raw):
        result = GardenStructureAgent(llm_api_key="test").plan(
            theme="AI Agent",
            garden_cards=[
                {"id": "card1", "title": "观点一", "one_liner": "瓶颈不在模型", "card_kind": "viewpoint"},
                {"id": "card2", "title": "观点二", "one_liner": "需要嵌入工作流", "card_kind": "viewpoint"},
                {"id": "card3", "title": "方法", "one_liner": "构筑工程防线", "card_kind": "method"},
            ],
        )

    assert result["status"] == "structured"
    assert result["core_claim"] == "AI Agent 的价值来自工程底座。"
    assert result["groups"][0]["role"] == "support"
    assert result["groups"][0]["card_ids"] == ["card1", "card2"]
    assert result["evidence_ids"] == ["cap1", "cap2"]


def test_plan_drops_unknown_card_ids_and_normalizes_roles():
    raw = json.dumps({
        "status": "structured",
        "core_claim": "核心判断",
        "groups": [
            {
                "role": "supporting_claims",
                "title": "支撑观点",
                "card_ids": ["card1", "missing"],
                "reason": "过滤未知 id。",
            },
            {
                "role": "weird",
                "title": "未知角色",
                "card_ids": ["card2"],
                "reason": "未知角色回退。",
            },
        ],
    }, ensure_ascii=False)

    with patch.object(GardenStructureAgent, "_call_llm", return_value=raw):
        result = GardenStructureAgent(llm_api_key="test").plan(
            theme="AI Agent",
            garden_cards=[
                {"id": "card1", "title": "观点一", "one_liner": "一", "card_kind": "viewpoint"},
                {"id": "card2", "title": "观点二", "one_liner": "二", "card_kind": "viewpoint"},
            ],
        )

    assert result["groups"][0]["role"] == "support"
    assert result["groups"][0]["card_ids"] == ["card1"]
    assert result["groups"][1]["role"] == "support"
    assert result["groups"][1]["card_ids"] == ["card2"]


def test_plan_returns_not_worth_canvas_when_llm_says_no_structure():
    raw = json.dumps({
        "status": "not_worth_canvas",
        "reason": "这些卡只是同主题，没有形成清晰判断。",
    }, ensure_ascii=False)

    with patch.object(GardenStructureAgent, "_call_llm", return_value=raw):
        result = GardenStructureAgent(llm_api_key="test").plan(
            theme="AI Agent",
            garden_cards=[
                {"id": "card1", "title": "观点一", "one_liner": "一", "card_kind": "viewpoint"},
            ],
        )

    assert result == {
        "status": "not_worth_canvas",
        "reason": "这些卡只是同主题，没有形成清晰判断。",
    }
