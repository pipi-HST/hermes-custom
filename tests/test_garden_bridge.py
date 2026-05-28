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

    assert result["status"] == "error"
    assert result["agent"] == "garden"
    assert result["errors"]


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


def test_synthesize_garden_returns_seed_card_with_accept_action(repo, tmp_path):
    """合成 Garden Card 后应返回 seed artifact 和确认采用 action。"""
    from bridge.garden_bridge import synthesize_garden

    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    (captures_dir / "cap.md").write_text(
        "---\ntitle: 测试素材\nstory: 摘要\n---\n\n正文内容\n",
        encoding="utf-8",
    )
    repo.conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, datetime('now'))",
        ("cap1", "captures/cap.md"),
    )
    repo.conn.commit()

    agent_result = {
        "title": "测试观点",
        "one_liner": "这是一个可继续演化的观点。",
        "core_arguments": [],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 测试观点\n",
    }
    db_path = str(Path(repo.conn.execute("PRAGMA database_list").fetchone()[2]))
    with patch("bridge.garden_bridge.load_config") as mock_cfg, \
            patch.object(GardenAgent, "synthesize", return_value=agent_result):
        mock_cfg.return_value = MagicMock(
            vault_path=str(tmp_path),
            db_path=db_path,
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        result = synthesize_garden(capture_ids=["cap1"])

    assert result["status"] == "success"
    artifact = result["durable_artifacts"][0]
    assert artifact["type"] == "garden_card"
    assert artifact["status"] == "seed"
    assert result["next_actions"][0]["intent"] == "accept_garden_card"
    assert result["next_actions"][0]["requires_confirmation"] is True
    row = repo.conn.execute(
        "SELECT status FROM garden_cards WHERE id = ?", (artifact["id"],)
    ).fetchone()
    assert row["status"] == "seed"
