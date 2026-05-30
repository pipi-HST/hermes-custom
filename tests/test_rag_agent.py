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
