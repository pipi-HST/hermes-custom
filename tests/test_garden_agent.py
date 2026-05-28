"""Tests for Garden Agent."""
from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock
from db.schema import init_db
from db.repo import CaptureRepo
from agents.garden_agent import GardenAgent, create_garden_card, update_garden_card


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    return CaptureRepo(conn)


@patch("agents.garden_agent.GardenAgent._call_llm")
def test_synthesize_two_step(mock_call):
    # Step 1 returns structured extraction, Step 2 returns links
    call_count = [0]

    def side_effect(prompt, max_tokens=2000):
        call_count[0] += 1
        if call_count[0] == 1:
            return json.dumps({
                "one_liner": "AI Agent 的记忆系统包含三层架构",
                "core_arguments": [
                    {"claim": "短期记忆用于当前任务", "consensus": True,
                     "source_ids": ["cap1"]},
                    {"claim": "长期记忆存储用户偏好", "consensus": False,
                     "source_ids": ["cap2"]},
                ],
                "key_materials": [
                    {"type": "quote", "content": "记忆是Agent的核心",
                     "capture_id": "cap1"},
                ],
            })
        else:
            return '["gc001", "gc002"]'

    mock_call.side_effect = side_effect

    agent = GardenAgent(llm_api_key="test")
    captures = [
        {"id": "cap1", "title": "Agent 记忆系统", "cached_text": "内容1"},
        {"id": "cap2", "title": "长期记忆设计", "cached_text": "内容2"},
    ]
    existing = [
        {"id": "gc001", "one_liner": "Agent 架构概述"},
        {"id": "gc002", "one_liner": "知识管理方法论"},
    ]

    result = agent.synthesize(captures, existing)
    assert "AI Agent" in result["one_liner"]
    assert len(result["core_arguments"]) == 2
    assert result["core_arguments"][0]["consensus"] is True
    assert len(result["key_materials"]) == 1
    assert "gc001" in result["knowledge_links"]
    assert "gc002" in result["knowledge_links"]
    assert result["markdown"]  # non-empty synthesis


def test_create_garden_card(repo):
    from db.repo import CaptureRepo
    card_id = create_garden_card(repo, {
        "title": "测试卡片",
        "one_liner": "核心结论",
        "core_arguments": [{"claim": "论点", "consensus": True, "source_ids": []}],
        "knowledge_links": [],
        "key_materials": [],
        "capture_ids": ["cap1"],
    })
    assert card_id
    row = repo.conn.execute(
        "SELECT * FROM garden_cards WHERE id = ?", (card_id,)
    ).fetchone()
    assert row is not None
    assert row["title"] == "测试卡片"
    assert row["status"] == "seed"


def test_update_garden_card(repo):
    from db.repo import CaptureRepo
    card_id = create_garden_card(repo, {
        "title": "原始标题", "one_liner": "原始结论",
        "core_arguments": [], "knowledge_links": [],
        "key_materials": [], "capture_ids": [],
    })
    updated = update_garden_card(repo, card_id, {
        "title": "更新标题",
        "one_liner": "更新结论",
        "core_arguments": [{"claim": "新论点", "consensus": True, "source_ids": []}],
        "knowledge_links": ["gc_other"],
        "key_materials": [],
    })
    assert updated is not None
    row = repo.conn.execute(
        "SELECT * FROM garden_cards WHERE id = ?", (card_id,)
    ).fetchone()
    assert row["title"] == "更新标题"
    assert row["status"] == "seed"


@patch("agents.garden_agent.GardenAgent._call_llm")
def test_synthesize_without_existing_cards(mock_call):
    mock_call.return_value = json.dumps({
        "one_liner": "核心结论",
        "core_arguments": [],
        "key_materials": [],
    })
    agent = GardenAgent(llm_api_key="test")
    result = agent.synthesize(
        [{"id": "1", "title": "测试", "cached_text": "内容"}],
        [],
    )
    assert result["knowledge_links"] == []
    assert mock_call.call_count == 1  # only step 1, no step 2
