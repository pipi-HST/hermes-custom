"""Tests for outliner."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock
from agents.outliner import Outliner


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
