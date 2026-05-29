"""Tests for content cleaning pipeline."""
from __future__ import annotations

import pytest
from agents.cleaner import clean_text, is_meaningful_content


def test_removes_ads_and_navigation():
    raw = """关注我们，获取更多精彩内容
长按识别二维码

正文开始
这是一篇关于 AI Agent 的文章
Agent 是当前最热门的话题之一

推荐阅读：
  - 如何学习 Python
  - 2025 年技术趋势

<end>"""
    result = clean_text(raw)
    assert "关注我们" not in result
    assert "长按识别二维码" not in result
    assert "推荐阅读" not in result
    assert "这是一篇关于 AI Agent 的文章" in result
    assert "Agent 是当前最热门的话题之一" in result


def test_normalizes_whitespace():
    raw = "第一段\n\n\n\n第二段\n\n\n第三段"
    result = clean_text(raw)
    assert "\n\n\n\n" not in result


def test_strips_html_like_tags():
    raw = '<div class="content">正文内容</div><span>多余</span>'
    result = clean_text(raw)
    assert "<div" not in result
    assert "正文内容" in result


def test_removes_voice_filler_words():
    raw = "嗯那个我今天嗯想跟大家说一下就是那个AI Agent 这个东西"
    result = clean_text(raw)
    assert "嗯那个" not in result
    assert "就是那个" not in result
    assert "AI Agent" in result


def test_empty_input_returns_empty():
    assert clean_text("") == ""
    assert clean_text("   \n  ") == ""


def test_preserves_real_content():
    raw = """这是一篇关于 AI Agent 的文章。

Agent 是一种能够自主规划和执行任务的智能系统。

它与传统 ChatBot 的区别在于：
1. Agent 可以调用工具
2. Agent 可以维护长期记忆
3. Agent 可以根据目标自主决策"""
    result = clean_text(raw)
    assert "AI Agent" in result
    assert "自主规划" in result
    assert "ChatBot" in result
    assert "长期记忆" in result


def test_meaningful_content_accepts_english_articles():
    text = (
        "Over the past year, we have worked with dozens of teams building "
        "large language model agents across industries. Consistently, the "
        "most successful implementations were not using complex frameworks "
        "or specialized libraries. Instead, they were building with simple, "
        "composable patterns that make the system easier to inspect and improve."
    )

    assert is_meaningful_content(text) is True


def test_meaningful_content_rejects_extraction_failure_placeholder():
    text = (
        "[内容未能提取] https://www.anthropic.com/engineering/building-effective-agents\n"
        "请复制粘贴正文给我。"
    )

    assert is_meaningful_content(text) is False
