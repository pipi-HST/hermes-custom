"""Tests for garden_bridge bridge layer."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from db.schema import init_db
from db.repo import CaptureRepo
from agents.garden_agent import GardenAgent


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    return CaptureRepo(conn)


def test_synthesize_garden_returns_error_when_no_ids(repo):
    """无 capture_ids 或 question_id 时应返回错误。"""
    from bridge.garden_bridge import synthesize_garden

    # Patch load_config to use tmp_path
    with patch("bridge.garden_bridge.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            vault_path="/tmp/nonexistent",
            db_path=str(Path(repo.conn.execute("PRAGMA database_list").fetchone()[2])),
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        result = synthesize_garden(capture_ids=None, question_id="", title="")

    assert result["success"] is False
    assert "error" in result


def test_hydrate_reads_body_from_md(repo, tmp_path):
    """_hydrate_capture 应从 .md 文件读取 cached_text。"""
    from bridge.garden_bridge import _hydrate_capture

    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    md = (
        "---\n"
        "title: 测试卡片\n"
        "story: 一句话摘要\n"
        "tags:\n  - ai\n"
        "---\n"
        "这是正文内容，用于测试 hydration。\n"
    )
    (captures_dir / "test.md").write_text(md)

    row = {"id": "abc123", "file_path": "captures/test.md"}
    result = _hydrate_capture(tmp_path, row)

    assert result["title"] == "测试卡片"
    assert result["summary"] == "一句话摘要"
    assert "正文内容" in result["cached_text"]


def test_hydrate_returns_row_unchanged_when_no_file(repo, tmp_path):
    """无 file_path 时应原样返回。"""
    from bridge.garden_bridge import _hydrate_capture

    row = {"id": "abc123", "file_path": ""}
    result = _hydrate_capture(tmp_path, row)
    assert result == row
    assert "cached_text" not in result
