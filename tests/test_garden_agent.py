"""Tests for Garden Agent."""
from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock
from db.schema import init_db
from db.repo import CaptureRepo
from agents.garden_agent import (
    GardenAgent,
    commit_garden_draft_to_card,
    create_garden_card,
    create_garden_draft,
    get_garden_draft,
    get_latest_open_garden_draft,
    update_garden_card,
    update_garden_draft,
)


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


@patch("agents.garden_agent.GardenAgent._call_llm")
def test_synthesize_renders_card_markdown_not_internal_prompt(mock_call):
    mock_call.return_value = json.dumps({
        "title": "按不确定性调节 Agent 复杂度",
        "one_liner": "AI Agent 复杂度应按任务不确定性递增",
        "core_arguments": [
            {
                "claim": "先用简单工作流解决稳定问题，再把开放任务交给 Agent。",
                "consensus": True,
                "source_ids": ["cap1", "cap2"],
            }
        ],
        "key_materials": [
            {
                "type": "quote",
                "content": "始终从最简单的方案开始。",
                "capture_id": "cap1",
            }
        ],
    })
    agent = GardenAgent(llm_api_key="test")

    result = agent.synthesize(
        [{"id": "cap1", "title": "Agent 设计", "cached_text": "内容"}],
        [],
    )

    assert result["markdown"].startswith("# 按不确定性调节 Agent 复杂度")
    assert "先用简单工作流" in result["markdown"]
    assert "始终从最简单的方案开始" in result["markdown"]
    assert "将以下结构化信息" not in result["markdown"]
    assert "输出 Markdown" not in result["markdown"]


@patch("agents.garden_agent.GardenAgent._call_llm")
def test_synthesize_uses_distinct_title_when_model_provides_one(mock_call):
    mock_call.return_value = json.dumps({
        "title": "AI 落地卡在工程底座",
        "one_liner": "AI 的价值不是由模型单独决定，而是由工程底座和工作流整合决定。",
        "core_arguments": [
            {"claim": "模型能力只有嵌入真实流程才会转化为组织收益。", "source_ids": ["cap1"]},
        ],
        "key_materials": [],
    })
    agent = GardenAgent(llm_api_key="test")

    result = agent.synthesize(
        [{"id": "cap1", "title": "AI 企业落地", "cached_text": "内容"}],
        [],
    )

    assert result["title"] == "AI 落地卡在工程底座"
    assert result["title"] != result["one_liner"]
    assert result["markdown"].startswith("# AI 落地卡在工程底座")


@patch("agents.garden_agent.GardenAgent._call_llm")
def test_synthesize_renders_focused_garden_card_instead_of_material_dump(mock_call):
    mock_call.return_value = json.dumps({
        "title": "AI 落地卡在组织转译",
        "one_liner": "AI 不是自动产生价值的工具，只有被转译进工程约束和日常流程才会产生组织收益。",
        "core_arguments": [
            {"claim": f"支撑判断 {idx}", "consensus": idx % 2 == 0, "source_ids": [f"cap{idx}"]}
            for idx in range(1, 11)
        ],
        "key_materials": [
            {"type": "quote", "content": f"关键材料 {idx}", "capture_id": f"cap{idx}"}
            for idx in range(1, 9)
        ],
    })
    agent = GardenAgent(llm_api_key="test")

    result = agent.synthesize(
        [{"id": f"cap{idx}", "title": f"材料 {idx}", "cached_text": "内容"}
         for idx in range(1, 6)],
        [],
    )

    markdown = result["markdown"]
    assert "## 我的判断" in markdown
    assert "## 为什么值得留下" in markdown
    assert "## 依据" in markdown
    assert "## 核心观点" not in markdown
    assert "## 支撑材料" not in markdown
    assert markdown.count("- 支撑判断") <= 4
    assert markdown.count("- 关键材料") <= 4


@patch("agents.garden_agent.GardenAgent._call_llm")
def test_synthesize_renders_knowledge_links_as_obsidian_links(mock_call):
    mock_call.side_effect = [
        json.dumps({
            "title": "AI 落地卡在工程底座",
            "one_liner": "AI 落地需要工程底座和工作流承接。",
            "core_arguments": [
                {"claim": "没有工程约束，AI 会放大混乱。", "source_ids": ["cap1"]},
            ],
            "key_materials": [],
        }),
        json.dumps(["23a4f10b"]),
    ]
    agent = GardenAgent(llm_api_key="test")

    result = agent.synthesize(
        [{"id": "cap1", "title": "AI 企业落地", "cached_text": "内容"}],
        [{
            "id": "23a4f10bc173",
            "title": "AI价值取决于工程底座与主动工作流整合",
            "one_liner": "AI价值取决于工程底座与主动工作流整合",
        }],
    )

    assert result["knowledge_links"] == ["23a4f10bc173"]
    assert (
        "- [[garden/AI价值取决于工程底座与主动工作流整合-23a4f10bc173|"
        "AI价值取决于工程底座与主动工作流整合]]"
    ) in result["markdown"]
    assert "\n- 23a4f10b\n" not in result["markdown"]


@patch("agents.garden_agent.GardenAgent._call_llm")
def test_step1_prompt_requires_precise_single_source_materials(mock_call):
    mock_call.return_value = json.dumps({
        "title": "来源要精确",
        "one_liner": "Garden 依据必须能追溯到明确来源。",
        "core_arguments": [],
        "key_materials": [],
    })
    agent = GardenAgent(llm_api_key="test")

    agent.synthesize(
        [
            {"id": "cap1", "title": "材料一", "cached_text": "84% 从未用过 AI"},
            {"id": "cap2", "title": "材料二", "cached_text": "故障减少50%"},
        ],
        [],
    )

    prompt = mock_call.call_args.args[0]
    assert "每条 key_materials 只能来自一篇 capture" in prompt
    assert "混合多篇材料" in prompt
    assert "capture_id 必须对应" in prompt


@patch("agents.garden_agent.GardenAgent._call_llm")
def test_synthesize_repairs_invalid_extract_json_once(mock_call):
    mock_call.side_effect = [
        '{"one_liner": "坏掉的 JSON", "core_arguments": [{"claim": "未闭合"',
        json.dumps({
            "one_liner": "修复后的观点",
            "core_arguments": [{"claim": "JSON 修复后继续合成"}],
            "key_materials": [],
        }),
    ]
    agent = GardenAgent(llm_api_key="test")

    result = agent.synthesize(
        [{"id": "cap1", "title": "材料", "cached_text": "内容"}],
        [],
    )

    assert result["one_liner"] == "修复后的观点"
    assert result["core_arguments"][0]["claim"] == "JSON 修复后继续合成"
    assert mock_call.call_count == 2


@patch("agents.garden_agent.GardenAgent._call_llm")
def test_synthesize_requests_enough_tokens_for_reasoning_json(mock_call):
    mock_call.return_value = json.dumps({
        "one_liner": "多篇材料需要更大的 JSON 输出预算",
        "core_arguments": [],
        "key_materials": [],
    })
    agent = GardenAgent(llm_api_key="test")

    agent.synthesize(
        [{"id": "cap1", "title": "材料", "cached_text": "内容"}],
        [],
    )

    assert mock_call.call_args.kwargs["max_tokens"] >= 6000


@patch("agents.garden_agent.GardenAgent._call_llm")
def test_synthesize_returns_error_when_extract_json_cannot_be_repaired(mock_call):
    mock_call.return_value = '{"one_liner": "一直坏掉"'
    agent = GardenAgent(llm_api_key="test")

    result = agent.synthesize(
        [{"id": "cap1", "title": "材料", "cached_text": "内容"}],
        [],
    )

    assert "error" in result
    assert "结构化" in result["error"]


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


def test_create_and_get_garden_draft(repo):
    draft_id = create_garden_draft(repo, {
        "title": "测试观点草稿",
        "one_liner": "一个还没有确认的观点。",
        "core_arguments": [{"claim": "观点一", "source_ids": ["cap1"]}],
        "knowledge_links": ["gc1"],
        "key_materials": [{"type": "quote", "content": "素材", "capture_id": "cap1"}],
        "markdown": "# 测试观点草稿\n\n正文",
        "capture_ids": ["cap1"],
        "question_id": "q1",
        "instruction": "从这批材料提炼观点",
    })

    row = get_garden_draft(repo, draft_id)

    assert row is not None
    assert row["title"] == "测试观点草稿"
    assert row["status"] == "draft_ready"
    assert json.loads(row["capture_ids"]) == ["cap1"]
    assert json.loads(row["core_arguments"])[0]["claim"] == "观点一"


def test_get_latest_open_garden_draft_skips_committed(repo):
    old_id = create_garden_draft(repo, {
        "title": "旧草稿",
        "one_liner": "旧",
        "core_arguments": [],
        "knowledge_links": [],
        "key_materials": [],
        "markdown": "# 旧草稿\n",
        "capture_ids": [],
    })
    new_id = create_garden_draft(repo, {
        "title": "新草稿",
        "one_liner": "新",
        "core_arguments": [],
        "knowledge_links": [],
        "key_materials": [],
        "markdown": "# 新草稿\n",
        "capture_ids": [],
    })
    repo.conn.execute(
        "UPDATE garden_drafts SET status='committed' WHERE id=?",
        (new_id,),
    )
    repo.conn.commit()

    latest = get_latest_open_garden_draft(repo)

    assert latest["id"] == old_id


def test_update_garden_draft_changes_content_and_instruction(repo):
    draft_id = create_garden_draft(repo, {
        "title": "原始草稿",
        "one_liner": "原始",
        "core_arguments": [],
        "knowledge_links": [],
        "key_materials": [],
        "markdown": "# 原始草稿\n",
        "capture_ids": ["cap1"],
    })

    updated = update_garden_draft(repo, draft_id, {
        "title": "修改后草稿",
        "one_liner": "修改后",
        "core_arguments": [{"claim": "新观点"}],
        "knowledge_links": [],
        "key_materials": [],
        "markdown": "# 修改后草稿\n",
        "instruction": "更强调边界",
    })

    assert updated is not None
    row = get_garden_draft(repo, draft_id)
    assert row["title"] == "修改后草稿"
    assert row["instruction"] == "更强调边界"
    assert json.loads(row["core_arguments"])[0]["claim"] == "新观点"


def test_commit_garden_draft_to_card_creates_card_and_marks_draft(repo):
    draft_id = create_garden_draft(repo, {
        "title": "可入库观点",
        "one_liner": "确认后才能进入 garden。",
        "core_arguments": [{"claim": "草稿先审阅"}],
        "knowledge_links": [],
        "key_materials": [],
        "markdown": "# 可入库观点\n\n确认后内容。",
        "capture_ids": ["cap1"],
    })

    card_id = commit_garden_draft_to_card(repo, draft_id)

    assert card_id
    card = repo.conn.execute(
        "SELECT * FROM garden_cards WHERE id=?",
        (card_id,),
    ).fetchone()
    draft = get_garden_draft(repo, draft_id)
    assert card["title"] == "可入库观点"
    assert card["status"] == "seed"
    assert draft["status"] == "committed"
    assert draft["committed_card_id"] == card_id


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
