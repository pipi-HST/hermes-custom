"""Tests for summarizer."""
from __future__ import annotations

from unittest.mock import patch
import pytest

from bridge.ai_cancel import AIOperationCancelled, cancel_operation, operation_context
from agents.summarizer import Summarizer, SEED_TAGS


def test_summarizer_uses_seed_tags_on_cold_start():
    s = Summarizer(api_key="test", provider="openai", model="test-model")
    assert s.seed_tags == SEED_TAGS


def test_call_llm_respects_cancel_before_http(monkeypatch):
    s = Summarizer(api_key="test", provider="openai", model="test-model")

    def fail_client(*_args, **_kwargs):
        raise AssertionError("HTTP client should not be created after cancellation")

    monkeypatch.setattr("agents.summarizer.httpx.Client", fail_client)
    with operation_context("op-summarizer"):
        cancel_operation("op-summarizer")
        with pytest.raises(AIOperationCancelled) as exc_info:
            s._call_llm("请整理")

    assert exc_info.value.stage == "summarizer.before_llm"


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
    assert result["body_markdown"] == "# 智能问数落地\n\n正文只修正格式。"
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
    assert result["folder_suggestion"] == {
        "mode": "existing",
        "folder_path": "Inbox",
        "reason": "AI 整理结果解析失败，先放入 Inbox 等待人工确认。",
    }
    assert result["card_suggestion"]["should_generate"] is False
    assert result["card_suggestion"]["reason"]
    assert "解析失败" in result["warnings"][0]
    diagnostics = result["diagnostics"]
    assert diagnostics["parse_error_kind"] == "json_parse_failed"
    assert diagnostics["response_len"] == len(mock_call.return_value)
    assert diagnostics["response_head"].startswith("{")
    assert diagnostics["max_tokens"] == 4000
    assert diagnostics["prompt_len"] > len("用户原始正文")
    assert diagnostics["response_tail"].endswith('"半截 JSON"')
    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["json_mode"] is True


def test_parse_json_response_accepts_balanced_json_with_trailing_text():
    s = Summarizer(api_key="test")
    result = s._parse_json_response('{"summary": "可解析"}\n\n补充说明')

    assert result == {"summary": "可解析"}


def test_call_llm_json_mode_falls_back_when_provider_rejects_response_format(monkeypatch):
    calls: list[dict] = []

    class FakeResponse:
        def __init__(self, status_code: int, content: str = "{}"):
            self.status_code = status_code
            self._content = content

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise AssertionError("fallback response should be used before raising")

        def json(self) -> dict:
            return {"choices": [{"message": {"content": self._content}}]}

    class FakeClient:
        def __init__(self, timeout: int):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, _url: str, *, headers: dict, json: dict):
            calls.append(dict(json))
            if len(calls) == 1:
                return FakeResponse(400)
            return FakeResponse(200, '{"title": "可解析"}')

    monkeypatch.setattr("agents.summarizer.httpx.Client", FakeClient)
    s = Summarizer(api_key="test")

    result = s._call_llm("请返回 JSON", json_mode=True)

    assert result == '{"title": "可解析"}'
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1]


def test_organize_editable_draft_prompt_allows_structured_body_without_compression(monkeypatch):
    prompts: list[str] = []

    def fake_call(prompt: str, *, max_tokens: int = 4000, json_mode: bool = False) -> str:
        prompts.append(prompt)
        assert json_mode is True
        return '{"title": "长文整理", "summary": "长文整理出结构化正文。", "tags": ["draft"], "body_markdown": "## 背景\\n\\n正文内容", "folder_suggestion": {}, "card_suggestion": {}, "warnings": []}'

    s = Summarizer(api_key="test")
    monkeypatch.setattr(s, "_call_llm", fake_call)

    result = s.organize_editable_draft("正文内容" * 2000)

    assert result["summary"] == "长文整理出结构化正文。"
    assert result["body_markdown"] == "## 背景\n\n正文内容"
    assert "body_markdown" in prompts[0]
    assert "不得删减信息" in prompts[0]
    assert "不做压缩总结" in prompts[0]
    assert "只能从“已有文件夹”中选择 existing" in prompts[0]
    assert "未列出的分类" in prompts[0]


def test_organize_editable_draft_prompt_forbids_outline_body_pollution(monkeypatch):
    prompts: list[str] = []

    def fake_call(prompt: str, *, max_tokens: int = 4000, json_mode: bool = False) -> str:
        prompts.append(prompt)
        assert json_mode is True
        return '{"title": "结构整理", "summary": "摘要", "tags": ["draft"], "body_markdown": "正文", "folder_suggestion": {}, "card_suggestion": {}, "warnings": []}'

    s = Summarizer(api_key="test")
    monkeypatch.setattr(s, "_call_llm", fake_call)

    s.organize_editable_draft("## 目录纲要\n\n1 编排循环\n\nimage.png\n\n正文")

    prompt = prompts[0]
    assert "不得输出空标题" in prompt
    assert "不得输出只有 ### 或 ## 的空 Markdown 标题" in prompt
    assert "不得把目录纲要条目复制或混入正文段落" in prompt
    assert "图片占位：image.png" in prompt


def test_organize_editable_draft_prompt_preserves_body_length_and_paragraphs(monkeypatch):
    prompts: list[str] = []

    def fake_call(prompt: str, *, max_tokens: int = 4000, json_mode: bool = False) -> str:
        prompts.append(prompt)
        assert json_mode is True
        return '{"title": "保真整理", "summary": "摘要", "tags": ["draft"], "body_markdown": "正文", "folder_suggestion": {}, "card_suggestion": {}, "warnings": []}'

    s = Summarizer(api_key="test")
    monkeypatch.setattr(s, "_call_llm", fake_call)

    s.organize_editable_draft("第一段。\n\n第二段。\n\n第三段。" * 800)

    prompt = prompts[0]
    assert "只改变可读性" in prompt
    assert "不得把多段原文合并成概述" in prompt
    assert "body_markdown 长度不得显著短于原文" in prompt
    assert "至少保留原文约 80%" in prompt


def test_organize_editable_draft_scales_token_budget_for_long_body(monkeypatch):
    budgets: list[int] = []

    def fake_call(_prompt: str, *, max_tokens: int = 4000, json_mode: bool = False) -> str:
        budgets.append(max_tokens)
        assert json_mode is True
        return '{"title": "长文整理", "summary": "摘要", "tags": ["draft"], "body_markdown": "正文", "folder_suggestion": {}, "card_suggestion": {}, "warnings": []}'

    s = Summarizer(api_key="test")
    monkeypatch.setattr(s, "_call_llm", fake_call)

    s.organize_editable_draft("长文内容" * 1200)

    assert budgets[0] >= 12000


def test_organize_editable_draft_normalizes_empty_suggestions(monkeypatch):
    def fake_call(_prompt: str, *, max_tokens: int = 4000, json_mode: bool = False) -> str:
        assert json_mode is True
        return (
            '{"title": "文件夹建议", "summary": "摘要", "tags": ["draft"], '
            '"body_markdown": "", "folder_suggestion": {}, '
            '"card_suggestion": {}, "warnings": []}'
        )

    s = Summarizer(api_key="test")
    monkeypatch.setattr(s, "_call_llm", fake_call)

    result = s.organize_editable_draft("正文内容")

    assert result["folder_suggestion"]["folder_path"] == "Inbox"
    assert result["folder_suggestion"]["reason"]
    assert result["card_suggestion"]["should_generate"] is False
    assert result["card_suggestion"]["reason"]


def test_organize_editable_draft_metadata_reads_beyond_old_short_prefix(monkeypatch):
    prompts: list[str] = []

    def fake_call(prompt: str, *, max_tokens: int = 4000, json_mode: bool = False) -> str:
        prompts.append(prompt)
        assert json_mode is True
        return '{"title": "长文后段主题", "summary": "摘要需要看到后半段。", "tags": ["draft"], "body_markdown": "", "folder_suggestion": {}, "card_suggestion": {}, "warnings": []}'

    s = Summarizer(api_key="test")
    monkeypatch.setattr(s, "_call_llm", fake_call)

    s.organize_editable_draft(("前段内容" * 2000) + "后半段关键主题")

    assert "后半段关键主题" in prompts[0]
