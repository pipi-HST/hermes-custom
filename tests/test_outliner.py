"""Tests for outliner."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from agents.outliner import OUTLINE_MAX_TOKENS, OUTLINE_SOURCE_CHAR_LIMIT, Outliner


def test_outliner_skips_short_text():
    o = Outliner(api_key="test", model="deepseek-v4-pro",
                  base_url="https://api.deepseek.com")
    result = o.generate("短文本", word_count=500)
    assert result == []


@patch("agents.outliner.httpx.Client.post")
def test_outliner_generates_for_long_text(mock_post):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps([
            {"level": 1, "title": "概述", "range": "段1-2",
             "summary": "整体介绍"},
            {"level": 1, "title": "细节", "range": "段3-5",
             "summary": "深入分析"},
        ])}}]
    }
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    o = Outliner(api_key="test", model="deepseek-v4-pro",
                  base_url="https://api.deepseek.com")
    result = o.generate("长文本内容 " * 500, word_count=2500)
    assert len(result) == 2
    assert result[0]["title"] == "概述"
    assert result[0]["level"] == 1


def test_outliner_retry_config():
    o = Outliner(api_key="test", retries=3)
    assert o.retries == 3


@patch("agents.outliner.httpx.Client.post")
def test_outliner_strips_markdown_fences(mock_post):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "```json\n" + json.dumps([
            {"level": 1, "title": "主题", "range": "段1-2", "summary": "概要"},
        ]) + "\n```"}}]
    }
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    o = Outliner(api_key="test")
    result = o.generate("长文本 " * 500, word_count=2500)
    assert len(result) == 1
    assert result[0]["title"] == "主题"


@patch("agents.outliner.httpx.Client.post")
def test_outliner_extracts_json_array_from_explanatory_text(mock_post):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "下面是目录：\n" + json.dumps([
            {"level": 1, "title": "背景", "range": "段1-2", "summary": "介绍背景"},
        ], ensure_ascii=False) + "\n以上。"}}]
    }
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    o = Outliner(api_key="test")
    result = o.generate("长文本 " * 500, word_count=2500)

    assert len(result) == 1
    assert result[0]["title"] == "背景"


@patch("agents.outliner.httpx.Client.post")
def test_outliner_limits_source_text_and_uses_larger_output_budget(mock_post):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": "[]"}}]
    }
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    o = Outliner(api_key="test")
    o.generate("长文本" * 5000, word_count=2500)

    payload = mock_post.call_args.kwargs["json"]
    assert len(payload["messages"][0]["content"]) < OUTLINE_SOURCE_CHAR_LIMIT + 300
    assert payload["max_tokens"] == OUTLINE_MAX_TOKENS


@patch("agents.outliner.httpx.Client.post")
def test_outliner_reports_empty_token_limit_response(mock_post):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"finish_reason": "length", "message": {"content": ""}}]
    }
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    o = Outliner(api_key="test", retries=1)

    with pytest.raises(ValueError, match="token limit"):
        o.generate("长文本" * 5000, word_count=2500)
