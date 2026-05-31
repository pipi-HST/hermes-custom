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


def test_synthesize_garden_creates_reviewable_draft_not_card(repo, tmp_path):
    """Garden 合成应先生成草稿，不直接写入 garden_cards 或 garden/*.md。"""
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
        "core_arguments": [{"claim": "观点要先审阅", "source_ids": ["cap1"]}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 测试观点\n\n正文",
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
    assert artifact["type"] == "garden_draft"
    assert artifact["status"] == "draft_ready"
    assert result["next_actions"][0]["intent"] == "commit_garden_draft"
    assert result["next_actions"][0]["requires_confirmation"] is True
    assert "确认" in result["chat_preview"]
    assert "观点要先审阅" in result["chat_preview"]

    draft = repo.conn.execute(
        "SELECT * FROM garden_drafts WHERE id = ?",
        (artifact["id"],),
    ).fetchone()
    card_count = repo.conn.execute("SELECT count(*) FROM garden_cards").fetchone()[0]
    assert draft["title"] == "测试观点"
    assert card_count == 0
    assert not (tmp_path / "garden").exists()


def test_synthesize_garden_accepts_unique_capture_id_prefix(repo, tmp_path):
    """Garden 合成应接受唯一 capture id 前缀，避免主 Agent 从文件名取短 id 后失败。"""
    from bridge.garden_bridge import synthesize_garden

    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    (captures_dir / "cap.md").write_text(
        "---\ntitle: 前缀素材\nstory: 摘要\n---\n\n正文内容\n",
        encoding="utf-8",
    )
    repo.conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, datetime('now'))",
        ("2bafca34bdd7", "captures/cap.md"),
    )
    repo.conn.commit()

    agent_result = {
        "title": "前缀观点",
        "one_liner": "短 id 也应解析到唯一 capture。",
        "core_arguments": [{"claim": "避免无效重试", "source_ids": ["2bafca34bdd7"]}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 前缀观点\n\n正文",
    }
    db_path = str(Path(repo.conn.execute("PRAGMA database_list").fetchone()[2]))

    with patch("bridge.garden_bridge.load_config") as mock_cfg, \
            patch.object(GardenAgent, "synthesize", return_value=agent_result) as mock_synthesize:
        mock_cfg.return_value = MagicMock(
            vault_path=str(tmp_path),
            db_path=db_path,
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        result = synthesize_garden(capture_ids=["2bafca34"])

    assert result["status"] == "success"
    assert result["meta"]["capture_ids"] == ["2bafca34bdd7"]
    assert mock_synthesize.call_args.args[0][0]["id"] == "2bafca34bdd7"


def test_synthesize_garden_passes_card_kind_to_agent(repo, tmp_path):
    """Garden bridge 应把用户指定的卡片类型传给 GardenAgent。"""
    from bridge.garden_bridge import synthesize_garden

    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    (captures_dir / "cap.md").write_text(
        "---\ntitle: 方法素材\nstory: 摘要\n---\n\n正文内容\n",
        encoding="utf-8",
    )
    repo.conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, datetime('now'))",
        ("cap1", "captures/cap.md"),
    )
    repo.conn.commit()
    agent_result = {
        "title": "方法卡",
        "one_liner": "这是方法卡。",
        "card_kind": "method",
        "core_arguments": [{"claim": "先规划再执行"}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 方法卡\n",
    }
    db_path = str(Path(repo.conn.execute("PRAGMA database_list").fetchone()[2]))

    with patch("bridge.garden_bridge.load_config") as mock_cfg, \
            patch.object(GardenAgent, "synthesize", return_value=agent_result) as mock_synthesize:
        mock_cfg.return_value = MagicMock(
            vault_path=str(tmp_path),
            db_path=db_path,
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        result = synthesize_garden(capture_ids=["cap1"], card_kind="method")

    assert result["status"] == "success"
    assert result["meta"]["card_kind"] == "method"
    assert "方法卡" in result["chat_preview"]
    assert "卡片预览" in result["chat_preview"]
    assert "观点预览" not in result["chat_preview"]
    assert mock_synthesize.call_args.kwargs["card_kind"] == "method"


def test_commit_garden_draft_writes_card_file_and_marks_draft(repo, tmp_path):
    """确认 Garden Draft 后才写 garden_cards 和 Obsidian 文件。"""
    from agents.garden_agent import create_garden_draft
    from bridge.garden_bridge import commit_garden_draft

    draft_id = create_garden_draft(repo, {
        "title": "确认后的观点",
        "one_liner": "确认才进入 garden。",
        "core_arguments": [{"claim": "先草稿后入库"}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 确认后的观点\n\n正文",
        "capture_ids": ["cap1"],
    })
    db_path = str(Path(repo.conn.execute("PRAGMA database_list").fetchone()[2]))

    with patch("bridge.garden_bridge.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            vault_path=str(tmp_path),
            db_path=db_path,
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        result = commit_garden_draft(draft_id)

    assert result["status"] == "success"
    artifact = result["durable_artifacts"][0]
    assert artifact["type"] == "garden_card"
    assert artifact["status"] == "seed"
    assert artifact["path"].startswith("garden/")
    assert (tmp_path / artifact["path"]).exists()
    assert "# 确认后的观点" in (tmp_path / artifact["path"]).read_text(encoding="utf-8")

    draft = repo.conn.execute(
        "SELECT * FROM garden_drafts WHERE id=?",
        (draft_id,),
    ).fetchone()
    assert draft["status"] == "committed"
    assert draft["committed_card_id"] == artifact["id"]


def test_commit_garden_draft_writes_card_frontmatter(repo, tmp_path):
    """确认 Garden Draft 后，Obsidian 文件应包含 Garden card frontmatter。"""
    import yaml
    from agents.garden_agent import create_garden_draft
    from bridge.garden_bridge import commit_garden_draft

    draft_id = create_garden_draft(repo, {
        "title": "方法卡",
        "one_liner": "确认才进入 garden。",
        "card_kind": "method",
        "core_arguments": [],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 方法卡\n\n正文",
        "capture_ids": ["cap1"],
    })
    db_path = str(Path(repo.conn.execute("PRAGMA database_list").fetchone()[2]))

    with patch("bridge.garden_bridge.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            vault_path=str(tmp_path),
            db_path=db_path,
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        result = commit_garden_draft(draft_id)

    text = (tmp_path / result["durable_artifacts"][0]["path"]).read_text(encoding="utf-8")
    assert text.startswith("---\n")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    assert frontmatter["type"] == "garden_card"
    assert frontmatter["card_kind"] == "method"
    assert frontmatter["source_captures"] == ["cap1"]


def test_commit_garden_draft_rejects_internal_prompt_markdown(repo, tmp_path):
    """包含内部 prompt 痕迹的 Garden Draft 不能入库。"""
    from agents.garden_agent import create_garden_draft
    from bridge.garden_bridge import commit_garden_draft

    draft_id = create_garden_draft(repo, {
        "title": "坏草稿",
        "one_liner": "内部模板不应入库。",
        "core_arguments": [{"claim": "坏内容"}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "将以下结构化信息合并为一张知识卡片（Markdown 格式）。\n\n输出 Markdown：",
        "capture_ids": ["cap1"],
    })
    db_path = str(Path(repo.conn.execute("PRAGMA database_list").fetchone()[2]))

    with patch("bridge.garden_bridge.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            vault_path=str(tmp_path),
            db_path=db_path,
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        result = commit_garden_draft(draft_id)

    assert result["status"] == "error"
    assert "内部模板" in result["chat_preview"]
    assert not (tmp_path / "garden").exists()
    assert repo.conn.execute("SELECT count(*) FROM garden_cards").fetchone()[0] == 0
    draft = repo.conn.execute(
        "SELECT status, committed_card_id FROM garden_drafts WHERE id=?",
        (draft_id,),
    ).fetchone()
    assert draft["status"] == "draft_ready"
    assert draft["committed_card_id"] == ""


def test_revise_garden_draft_updates_latest_open_draft(repo, tmp_path):
    """用户提出修改意见时，默认修改最近一个开放 Garden Draft。"""
    from agents.garden_agent import create_garden_draft
    from bridge.garden_bridge import revise_garden_draft

    create_garden_draft(repo, {
        "title": "旧观点",
        "one_liner": "旧观点",
        "core_arguments": [],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 旧观点\n",
        "capture_ids": ["cap1"],
    })
    agent_result = {
        "title": "修改后的观点",
        "one_liner": "修改后更贴近用户意见。",
        "core_arguments": [{"claim": "保留原观点但强调边界"}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 修改后的观点\n\n新正文",
    }
    db_path = str(Path(repo.conn.execute("PRAGMA database_list").fetchone()[2]))

    with patch("bridge.garden_bridge.load_config") as mock_cfg, \
            patch.object(GardenAgent, "synthesize", return_value=agent_result) as mock_synthesize:
        mock_cfg.return_value = MagicMock(
            vault_path=str(tmp_path),
            db_path=db_path,
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        result = revise_garden_draft(instruction="强调边界")

    assert result["status"] == "success"
    assert result["durable_artifacts"][0]["type"] == "garden_draft"
    assert "保留原观点但强调边界" in result["chat_preview"]
    row = repo.conn.execute("SELECT * FROM garden_drafts").fetchone()
    assert row["title"] == "修改后的观点"
    assert row["instruction"] == "强调边界"
    assert any(
        c.get("title") == "用户修改意见" and "强调边界" in c.get("cached_text", "")
        for c in mock_synthesize.call_args.args[0]
    )
