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


def test_synthesize_garden_blocks_clear_duplicate_card(repo, tmp_path):
    from bridge.garden_bridge import synthesize_garden

    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    (captures_dir / "cap.md").write_text(
        "---\ntitle: Agent 素材\nstory: 摘要\n---\n\n正文内容\n",
        encoding="utf-8",
    )
    repo.conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, datetime('now'))",
        ("cap1", "captures/cap.md"),
    )
    repo.conn.execute(
        """INSERT INTO garden_cards
           (id, title, one_liner, card_kind, core_arguments, knowledge_links,
            key_materials, status, capture_ids)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'seed', ?)""",
        (
            "card1",
            "AI Agent 工程化",
            "AI Agent 工程化的关键是用流程和护栏管理不可靠模型。",
            "method",
            "[]",
            "[]",
            "[]",
            json.dumps(["cap1"], ensure_ascii=False),
        ),
    )
    repo.conn.commit()

    agent_result = {
        "title": "工程化 Agent",
        "one_liner": "AI Agent 工程化的关键是用流程和护栏管理不可靠模型。",
        "card_kind": "method",
        "core_arguments": [{"claim": "需要流程和护栏。"}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 工程化 Agent\n",
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
    assert result["durable_artifacts"][0]["type"] == "garden_card"
    assert result["durable_artifacts"][0]["id"] == "card1"
    assert result["meta"]["duplicate_status"] == "duplicate"
    assert "高度重复" in result["chat_preview"]
    assert repo.conn.execute("SELECT count(*) FROM garden_drafts").fetchone()[0] == 0


def test_synthesize_garden_warns_possible_duplicate_but_creates_draft(repo, tmp_path):
    from bridge.garden_bridge import synthesize_garden

    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    for capture_id in ("cap1", "cap2", "cap4"):
        (captures_dir / f"{capture_id}.md").write_text(
            f"---\ntitle: {capture_id}\nstory: 摘要\n---\n\n正文内容\n",
            encoding="utf-8",
        )
        repo.conn.execute(
            "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, datetime('now'))",
            (capture_id, f"captures/{capture_id}.md"),
        )
    repo.conn.execute(
        """INSERT INTO garden_cards
           (id, title, one_liner, card_kind, core_arguments, knowledge_links,
            key_materials, status, capture_ids)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'seed', ?)""",
        (
            "card1",
            "AI 工作流落地",
            "AI 落地需要嵌入业务流程，而不是只依赖模型能力。",
            "method",
            "[]",
            "[]",
            "[]",
            json.dumps(["cap1", "cap2", "cap3"], ensure_ascii=False),
        ),
    )
    repo.conn.commit()

    agent_result = {
        "title": "AI Agent 工具链",
        "one_liner": "AI Agent 要通过工具链进入真实工作流。",
        "card_kind": "method",
        "core_arguments": [{"claim": "工具链决定落地效果。"}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# AI Agent 工具链\n",
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
        result = synthesize_garden(capture_ids=["cap1", "cap2", "cap4"])

    assert result["status"] == "success"
    assert result["durable_artifacts"][0]["type"] == "garden_draft"
    assert result["warnings"]
    assert result["meta"]["duplicate_status"] == "possible_duplicate"
    assert "可能和已有知识卡片相近" in result["chat_preview"]
    assert repo.conn.execute("SELECT count(*) FROM garden_drafts").fetchone()[0] == 1


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


def test_synthesize_garden_preview_lists_all_used_sources(repo, tmp_path):
    """微信预览应列出实际参与合成的全部来源，而不是只露出部分材料。"""
    from bridge.garden_bridge import synthesize_garden

    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    capture_ids = []
    for index in range(5):
        capture_id = f"cap{index}"
        capture_ids.append(capture_id)
        (captures_dir / f"{capture_id}.md").write_text(
            f"---\ntitle: 来源 {index}\nstory: 摘要\n---\n\n正文内容\n",
            encoding="utf-8",
        )
        repo.conn.execute(
            "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, datetime('now'))",
            (capture_id, f"captures/{capture_id}.md"),
        )
    repo.conn.commit()
    agent_result = {
        "title": "综合卡",
        "one_liner": "需要能追溯全部来源。",
        "card_kind": "method",
        "core_arguments": [{"claim": "综合来自多篇材料"}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 综合卡\n",
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
        result = synthesize_garden(capture_ids=capture_ids)

    for index in range(5):
        assert f"来源 {index}" in result["chat_preview"]


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


def test_commit_theme_origin_garden_card_updates_source_theme_page(repo, tmp_path):
    """从 Theme 候选生成的 Knowledge Card 入库后，应回写来源 Theme 页。"""
    from agents.garden_agent import create_garden_draft
    from agents.markdown_writer import write_theme_page
    from agents.theme_agent import create_pending_question
    from bridge.garden_bridge import commit_garden_draft

    theme_path = write_theme_page(
        tmp_path,
        theme_name="AI Agent 工程化",
        body=(
            "# AI Agent 工程化\n\n"
            "## 主题判断\n\n这些材料共同讨论 Agent 工程化。\n\n"
            "## 相关材料\n\n- [[Agent A]]\n\n"
            "## 材料之间的关系\n\n- A 提供框架。\n\n"
            "## 可沉淀的知识卡片方向\n\n- 方法卡：工作流判断。\n"
        ),
        captures=[{"title": "Agent A", "file_path": "captures/a.md"}],
        garden_cards=[],
    )
    question = create_pending_question(
        repo,
        "theme",
        "这组材料可以沉淀成什么知识卡片？",
        context_ref=f"theme:{theme_path}",
        related_capture_ids=["cap1"],
    )
    draft_id = create_garden_draft(repo, {
        "title": "AI Agent 工程化",
        "one_liner": "Agent 工程化需要流程、工具和评估约束。",
        "core_arguments": [{"claim": "先草稿后入库"}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# AI Agent 工程化\n\n正文",
        "capture_ids": ["cap1"],
        "question_id": question["id"],
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
    assert result["meta"]["source_theme"] == theme_path
    assert result["meta"]["theme_sync_status"] == "synced"
    card_path = result["durable_artifacts"][0]["path"]
    card_text = (tmp_path / card_path).read_text(encoding="utf-8")
    assert f"source_theme: {theme_path}" in card_text

    question_row = repo.conn.execute(
        "SELECT status, matched_response FROM pending_questions WHERE id=?",
        (question["id"],),
    ).fetchone()
    assert question_row["status"] == "answered"
    assert question_row["matched_response"] == "synthesized"

    theme_text = (tmp_path / theme_path).read_text(encoding="utf-8")
    assert "related_garden_cards:" in theme_text
    assert card_path in theme_text
    assert theme_text.count("[[AI Agent 工程化]]") == 1

    from agents.markdown_writer import append_garden_card_to_theme_page

    append_garden_card_to_theme_page(
        tmp_path,
        theme_path,
        card_title="AI Agent 工程化",
        card_path=card_path,
    )
    theme_text_after = (tmp_path / theme_path).read_text(encoding="utf-8")
    assert theme_text_after.count("[[AI Agent 工程化]]") == 1


def test_commit_garden_draft_writes_card_frontmatter(repo, tmp_path):
    """确认 Garden Draft 后，Obsidian Properties 应面向人可读。"""
    import yaml
    from agents.garden_agent import create_garden_draft
    from bridge.garden_bridge import commit_garden_draft

    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    (captures_dir / "cap.md").write_text(
        "---\ntitle: 来源文章\n---\n\n正文",
        encoding="utf-8",
    )
    repo.conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, datetime('now'))",
        ("cap1", "captures/cap.md"),
    )
    repo.conn.execute(
        """INSERT INTO garden_cards
           (id, title, one_liner, card_kind, core_arguments, knowledge_links,
            key_materials, status, capture_ids)
           VALUES (?, ?, ?, ?, '[]', '[]', '[]', 'seed', '[]')""",
        ("card1", "相关卡片", "相关卡片摘要", "method"),
    )
    repo.conn.commit()
    draft_id = create_garden_draft(repo, {
        "title": "方法卡",
        "one_liner": "确认才进入 garden。",
        "card_kind": "method",
        "core_arguments": [],
        "key_materials": [],
        "knowledge_links": ["card1"],
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
    assert "id" not in frontmatter
    assert "source_captures" not in frontmatter
    assert "related_cards" not in frontmatter
    assert frontmatter["sources"] == ["[[captures/cap|来源文章]]"]
    assert frontmatter["related"] == ["[[garden/相关卡片-card1|相关卡片]]"]


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
