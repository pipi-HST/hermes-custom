"""Tests for RAG agent."""
from __future__ import annotations

from unittest.mock import patch
from agents.rag_agent import RagAgent, format_answer, build_rag_prompt


def test_format_answer_with_sources():
    answer = "RAG 有三种架构：朴素、高级和模块化。"
    sources = [
        {"title": "RAG 技术综述", "story": "介绍了三种 RAG 架构",
         "file_path": "captures/rag.md", "summary": "旧字段兼容"},
    ]
    result = format_answer(answer, sources)
    assert "RAG 有三种架构" in result
    assert "RAG 技术综述" in result
    assert "[1]" in result


def test_format_answer_only_lists_cited_sources():
    answer = "这个回答只使用第一篇材料。[1]"
    sources = [
        {"title": "真正来源", "story": "被引用的材料", "file_path": "captures/real.md"},
        {"title": "候选材料", "story": "只是检索候选", "file_path": "captures/candidate.md"},
    ]

    result = format_answer(answer, sources)

    assert "[1] 真正来源" in result
    assert "候选材料" not in result
    assert "[2]" not in result


def test_format_answer_removes_dangling_partial_citation():
    answer = "AI Agent 工程化依赖 Harness 约束层。[1][2]["
    sources = [
        {"title": "来源一", "story": "", "file_path": "captures/one.md"},
        {"title": "来源二", "story": "", "file_path": "captures/two.md"},
    ]

    result = format_answer(answer, sources)

    body = result.split("\n\n📎 来源", 1)[0]
    assert body.endswith("[1][2]")
    assert not body.endswith("[")


def test_format_answer_does_not_truncate_inside_citation_marker():
    answer = "这是一段很长的回答。" * 40 + "[1][2]"
    sources = [
        {"title": "来源一", "story": "", "file_path": "captures/one.md"},
        {"title": "来源二", "story": "", "file_path": "captures/two.md"},
    ]

    result = format_answer(answer, sources)

    body = result.split("\n\n📎 来源", 1)[0]
    assert not body.endswith("[")
    assert not body.endswith("[1")


def test_format_answer_without_url():
    sources = [{"title": "测试", "story": "概要", "file_path": ""}]
    result = format_answer("答案", sources)
    assert "测试" in result
    assert "概要" in result


def test_build_prompt_includes_context():
    captures = [
        {"title": "RAG 入门", "story": "RAG 简介",
         "body": "RAG即检索增强生成。"},
    ]
    prompt = build_rag_prompt("什么是 RAG？", captures[:1])
    assert "什么是 RAG？" in prompt
    assert "RAG即检索增强生成" in prompt
    assert "RAG 入门" in prompt


def test_build_prompt_falls_back_to_summary():
    captures = [{"title": "测试", "story": "只用摘要", "body": ""}]
    prompt = build_rag_prompt("query", captures)
    assert "只用摘要" in prompt


def test_build_prompt_includes_garden_and_theme_as_orientation_only():
    captures = [{"title": "材料", "story": "", "body": "正文证据"}]
    context_pack = {
        "garden_cards": [
            {"title": "AI 工程化", "one_liner": "复杂度要受控。"}
        ],
        "theme_pages": [
            {"title": "Agent 方法论", "path": "themes/Agent 方法论.md"}
        ],
    }

    prompt = build_rag_prompt("怎么理解？", captures, context_pack=context_pack)

    assert "高层线索" in prompt
    assert "AI 工程化" in prompt
    assert "Agent 方法论" in prompt
    assert "不要把高层线索当作编号来源" in prompt
    assert "[1] 《材料》" in prompt


def test_build_prompt_includes_graph_theme_groups_as_orientation_only():
    captures = [{"title": "材料", "story": "", "body": "正文证据"}]
    context_pack = {
        "garden_cards": [
            {"title": "Agent 工程化", "one_liner": "用流程和护栏管理模型。"}
        ],
        "theme_groups": [
            {"title": "复杂度控制", "capture_ids": ["cap1", "cap2"]}
        ],
        "graph_trace": {"seed_count": 1, "garden_count": 1, "theme_count": 1, "final_count": 1},
    }

    prompt = build_rag_prompt("怎么理解？", captures, context_pack=context_pack)

    assert "检索路径" in prompt
    assert "复杂度控制" in prompt
    assert "Garden/Theme 是理解线索" in prompt
    assert "[1] 《材料》" in prompt


@patch("agents.rag_agent.RagAgent._call_llm")
def test_rag_query_returns_answer_and_sources(mock_call):
    mock_call.return_value = "RAG 是一种技术。"
    agent = RagAgent(llm_api_key="test", llm_model="deepseek-v4-pro",
                      llm_base_url="https://api.deepseek.com")
    captures = [{
        "id": "1", "title": "测试文章", "story": "test",
        "body": "RAG 即检索增强生成。",
        "file_path": "captures/test.md",
    }]
    result = agent.query("什么是 RAG？", captures)
    assert result["answer"] == "RAG 是一种技术。"
    assert len(result["sources"]) == 1
    assert result["sources"][0]["title"] == "测试文章"


@patch("agents.rag_agent.RagAgent._call_llm")
def test_rag_query_empty_captures(mock_call):
    agent = RagAgent(llm_api_key="test")
    result = agent.query("问题", [])
    assert "没有找到" in result["answer"]
    assert result["sources"] == []
