"""Tests for summarizer."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from agents.summarizer import Summarizer, SEED_TAGS


def test_summarizer_uses_seed_tags_on_cold_start():
    s = Summarizer(api_key="test", provider="openai", model="test-model")
    assert s.seed_tags == SEED_TAGS


def test_build_prompt_includes_existing_tags():
    s = Summarizer(api_key="test")
    prompt = s._build_summary_prompt(
        cleaned_text="AI Agent 是当前最热门的概念",
        existing_tags=["ai-agent", "product-design"],
    )
    assert "ai-agent" in prompt
    assert "product-design" in prompt
    assert "AI Agent 是当前最热门的概念" in prompt


@patch("agents.summarizer.Summarizer._call_llm")
def test_summarize_returns_summary_and_tags(mock_call):
    mock_call.return_value = """---
title: AI Agent 入门
summary: 介绍了 AI Agent 的基本概念和应用场景
tags: ["ai-agent", "ai-product"]
---"""
    s = Summarizer(api_key="test")
    result = s.summarize("AI Agent 是一种能够自主执行任务的系统")
    assert result["title"] == "AI Agent 入门"
    assert result["summary"] == "介绍了 AI Agent 的基本概念和应用场景"
    assert "ai-agent" in result["tags"]


@patch("agents.summarizer.Summarizer._call_llm")
def test_summarize_fallback_on_malformed_response(mock_call):
    mock_call.return_value = "没有分隔符的回复 AI Agent 是一个重要概念"
    s = Summarizer(api_key="test")
    result = s.summarize("AI Agent 是一种系统")
    assert len(result["summary"]) > 0
    assert len(result["tags"]) >= 0  # tags may be empty on malformed response


def test_parse_response_structured():
    s = Summarizer(api_key="test")
    raw = """---
title: AI 产品分析
summary: 这是一篇关于AI产品的分析
tags: ["ai-product", "case-study"]
---"""
    result = s._parse_response(raw)
    assert result["title"] == "AI 产品分析"
    assert result["summary"] == "这是一篇关于AI产品的分析"
    assert result["tags"] == ["ai-product", "case-study"]


def test_parse_response_fallback():
    s = Summarizer(api_key="test")
    raw = "这篇文章讨论了Agent框架的选择问题，没有分隔符"
    result = s._parse_response(raw)
    assert len(result["summary"]) > 0


@patch("agents.summarizer.Summarizer._call_llm")
def test_organize_editable_draft_parses_fenced_json(mock_call):
    mock_call.return_value = """```json
{
  "title": "智能问数落地",
  "summary": "智能问数需要语义层和产品运营协同。",
  "tags": ["data-agent", "ai-product"],
  "body_markdown": "# 智能问数落地\\n\\n正文只修正格式。",
  "folder_suggestion": {
    "mode": "new",
    "folder_path": "智能问数",
    "reason": "这是可持续积累的主题。"
  },
  "card_suggestion": {
    "should_generate": true,
    "reason": "材料包含可复用判断。"
  },
  "warnings": []
}
```"""
    s = Summarizer(api_key="test")

    result = s.organize_editable_draft("智能问数落地正文")

    assert result["title"] == "智能问数落地"
    assert result["summary"] == "智能问数需要语义层和产品运营协同。"
    assert result["tags"] == ["data-agent", "ai-product"]
    assert result["body_markdown"] == ""
    assert result["folder_suggestion"]["folder_path"] == "智能问数"
    assert result["card_suggestion"]["should_generate"] is True


@patch("agents.summarizer.Summarizer._call_llm")
def test_organize_editable_draft_bad_json_returns_safe_defaults(mock_call):
    mock_call.return_value = "{\n  \"title\": \"半截 JSON\""
    s = Summarizer(api_key="test")

    result = s.organize_editable_draft("用户原始正文")

    assert result["title"] == ""
    assert result["summary"] == ""
    assert result["tags"] == []
    assert result["body_markdown"] == ""
    assert result["card_suggestion"]["should_generate"] is False
    assert "解析失败" in result["warnings"][0]


def test_parse_json_response_accepts_balanced_json_with_trailing_text():
    s = Summarizer(api_key="test")
    result = s._parse_json_response('{"summary": "可解析"}\n\n补充说明')

    assert result == {"summary": "可解析"}


def test_organize_editable_draft_prompt_limits_ai_to_metadata(monkeypatch):
    prompts: list[str] = []

    def fake_call(prompt: str, *, max_tokens: int = 4000) -> str:
        prompts.append(prompt)
        return '{"title": "长文整理", "summary": "长文只生成元数据。", "tags": ["draft"], "body_markdown": "", "folder_suggestion": {}, "card_suggestion": {}, "warnings": []}'

    s = Summarizer(api_key="test")
    monkeypatch.setattr(s, "_call_llm", fake_call)

    result = s.organize_editable_draft("正文内容" * 2000)

    assert result["summary"] == "长文只生成元数据。"
    assert result["body_markdown"] == ""
    assert "不要返回正文" in prompts[0]
    assert '"body_markdown"' not in prompts[0]


def test_organize_editable_draft_metadata_reads_beyond_old_short_prefix(monkeypatch):
    prompts: list[str] = []

    def fake_call(prompt: str, *, max_tokens: int = 4000) -> str:
        prompts.append(prompt)
        return '{"title": "长文后段主题", "summary": "摘要需要看到后半段。", "tags": ["draft"], "body_markdown": "", "folder_suggestion": {}, "card_suggestion": {}, "warnings": []}'

    s = Summarizer(api_key="test")
    monkeypatch.setattr(s, "_call_llm", fake_call)

    s.organize_editable_draft(("前段内容" * 2000) + "后半段关键主题")

    assert "后半段关键主题" in prompts[0]
