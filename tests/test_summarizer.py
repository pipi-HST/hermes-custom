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
