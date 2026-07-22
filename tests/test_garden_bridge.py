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


def test_synthesize_garden_canonicalizes_short_payload_source_ids(repo, tmp_path):
    """Agent 返回短 source_ids 时，草稿 payload 应保存完整 capture id。"""
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
        "one_liner": "短 id 也应持久化为完整 capture。",
        "payload": {
            "title": "前缀观点",
            "claim": "短 id 也应持久化为完整 capture。",
            "source_ids": ["2bafca34"],
            "core_judgments": [
                {
                    "statement": "详情页来源依赖完整 capture id。",
                    "support": [
                        {"summary": "支持依据", "source_ids": ["2bafca34"]},
                    ],
                }
            ],
        },
        "core_arguments": [{"claim": "详情页来源依赖完整 capture id。", "source_ids": ["2bafca34"]}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 前缀观点\n\n正文",
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
        result = synthesize_garden(capture_ids=["2bafca34"])

    assert result["status"] == "success"
    draft = repo.conn.execute(
        "SELECT capture_ids, payload_json, core_arguments FROM garden_drafts WHERE id = ?",
        (result["meta"]["draft_id"],),
    ).fetchone()
    payload = json.loads(draft["payload_json"])

    assert json.loads(draft["capture_ids"]) == ["2bafca34bdd7"]
    assert payload["source_ids"] == ["2bafca34bdd7"]
    assert payload["core_judgments"][0]["support"][0]["source_ids"] == ["2bafca34bdd7"]
    assert json.loads(draft["core_arguments"])[0]["source_ids"] == ["2bafca34bdd7"]


def test_synthesize_garden_resolves_committed_capture_draft_id(repo, tmp_path):
    """刚入库后误传 draft id 时，应解析到 committed capture id。"""
    from bridge.garden_bridge import synthesize_garden

    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    (captures_dir / "cap.md").write_text(
        "---\ntitle: 已入库素材\nstory: 摘要\n---\n\n正文内容\n",
        encoding="utf-8",
    )
    repo.conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, datetime('now'))",
        ("cap-committed", "captures/cap.md"),
    )
    repo.conn.execute(
        """INSERT INTO capture_drafts
           (id, status, committed_capture_id, title, draft_text, raw_text)
           VALUES (?, 'committed', ?, '已入库素材', '正文内容', '原文')""",
        ("draft-committed", "cap-committed"),
    )
    repo.conn.commit()
    agent_result = {
        "title": "已入库观点",
        "one_liner": "误传 draft id 也应合成成功。",
        "core_arguments": [{"claim": "使用 committed capture。"}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 已入库观点\n",
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
        result = synthesize_garden(capture_ids=["draft-committed"])

    assert result["status"] == "success"
    assert result["meta"]["capture_ids"] == ["cap-committed"]
    assert result["meta"]["resolved_draft_ids"] == {"draft-committed": "cap-committed"}
    assert result["warnings"]
    assert mock_synthesize.call_args.args[0][0]["id"] == "cap-committed"


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


def test_commit_garden_draft_creates_structured_card_and_marks_draft(repo, tmp_path):
    """确认 Garden Draft 后只写 garden_cards，不创建常驻 garden Markdown。"""
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
        result = commit_garden_draft(draft_id, confirmation_source="manual")

    assert result["status"] == "success"
    artifact = result["durable_artifacts"][0]
    assert artifact["type"] == "garden_card"
    assert artifact["status"] == "seed"
    assert artifact.get("path", "") == ""
    assert result["meta"]["storage"] == "sqlite"
    assert not (tmp_path / "garden").exists()

    draft = repo.conn.execute(
        "SELECT * FROM garden_drafts WHERE id=?",
        (draft_id,),
    ).fetchone()
    assert draft["status"] == "committed"
    assert draft["committed_card_id"] == artifact["id"]
    card = repo.conn.execute("SELECT * FROM garden_cards WHERE id=?", (artifact["id"],)).fetchone()
    assert card["title"] == "确认后的观点"
    assert card["status"] == "seed"


def test_archive_garden_card_records_write_job(repo, tmp_path):
    """归档知识卡片应留下可审计的写入补偿记录。"""
    from agents.garden_agent import create_garden_card
    from bridge.garden_bridge import archive_garden_card

    card_id = create_garden_card(repo, {
        "title": "待归档观点",
        "one_liner": "归档只影响卡片库状态。",
        "core_arguments": [],
        "key_materials": [],
        "knowledge_links": [],
        "capture_ids": [],
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
        result = archive_garden_card(card_id)

    assert result["status"] == "success"
    card = repo.conn.execute(
        "SELECT status FROM garden_cards WHERE id = ?",
        (card_id,),
    ).fetchone()
    assert card["status"] == "archived"
    job = repo.conn.execute(
        """SELECT * FROM knowledge_write_jobs
           WHERE intent = 'archive_garden_card' AND object_id = ?""",
        (card_id,),
    ).fetchone()
    assert job["status"] == "completed"
    assert job["db_table"] == "garden_cards"


def test_commit_theme_origin_garden_card_marks_question_without_theme_markdown(repo, tmp_path):
    """从 Theme View 候选生成的 Knowledge Card 入库后，不再回写 Theme Markdown。"""
    from agents.garden_agent import create_garden_draft
    from agents.theme_agent import create_pending_question
    from bridge.garden_bridge import commit_garden_draft

    question = create_pending_question(
        repo,
        "theme",
        "这组材料可以沉淀成什么知识卡片？",
        context_ref="theme_view:cap1",
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
        result = commit_garden_draft(draft_id, confirmation_source="manual")

    assert result["status"] == "success"
    assert result["durable_artifacts"][0].get("path", "") == ""
    assert not (tmp_path / "themes").exists()
    assert not (tmp_path / "garden").exists()

    question_row = repo.conn.execute(
        "SELECT status, matched_response FROM pending_questions WHERE id=?",
        (question["id"],),
    ).fetchone()
    assert question_row["status"] == "answered"
    assert question_row["matched_response"] == "synthesized"

def test_commit_garden_draft_persists_card_payload_without_markdown(repo, tmp_path):
    """Knowledge Card 入库以 SQLite 结构化对象为准。"""
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
        result = commit_garden_draft(draft_id, confirmation_source="manual")

    assert result["durable_artifacts"][0].get("path", "") == ""
    assert not (tmp_path / "garden").exists()
    card = repo.conn.execute(
        "SELECT * FROM garden_cards WHERE id = ?",
        (result["durable_artifacts"][0]["id"],),
    ).fetchone()
    assert card["title"] == "方法卡"
    assert card["card_kind"] == "method"
    assert json.loads(card["knowledge_links"]) == ["card1"]
    assert json.loads(card["capture_ids"]) == ["cap1"]


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
        result = commit_garden_draft(draft_id, confirmation_source="manual")

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


def test_commit_garden_draft_requires_explicit_confirmation(repo, tmp_path):
    from agents.garden_agent import create_garden_draft
    from bridge.garden_bridge import commit_garden_draft

    draft_id = create_garden_draft(repo, {
        "title": "待确认卡片",
        "one_liner": "不能由模型直接入库。",
        "core_arguments": [],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 待确认卡片\n",
        "capture_ids": [],
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
    assert "确认卡片" in result["chat_preview"]
    assert repo.conn.execute("SELECT count(*) FROM garden_cards").fetchone()[0] == 0


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
