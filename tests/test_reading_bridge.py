"""Tests for Reading Session bridge."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from db.schema import init_db


@pytest.fixture
def reading_config(tmp_path):
    db_path = tmp_path / "test.db"
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    init_db(str(db_path)).close()
    return SimpleNamespace(db_path=str(db_path), vault_path=str(vault_path))


def test_start_reading_session_does_not_create_capture_draft(reading_config):
    from bridge import reading_bridge

    with patch.object(reading_bridge, "load_config", return_value=reading_config):
        reading_bridge._config = None
        reading_bridge._repo = None
        result = reading_bridge.start_reading_session(
            message="这段什么意思？",
            source_url="https://example.com/article",
        )

    assert result["status"] == "success"
    assert result["agent"] == "reading"
    assert result["intent"] == "start_reading_session"
    assert result["durable_artifacts"][0]["type"] == "reading_session"
    assert result["meta"]["source_type"] == "url"

    conn = init_db(reading_config.db_path)
    assert conn.execute("SELECT count(*) FROM reading_sessions").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM capture_drafts").fetchone()[0] == 0


def test_discuss_reading_records_turn_and_evidence(reading_config):
    from bridge import reading_bridge

    with patch.object(reading_bridge, "load_config", return_value=reading_config):
        reading_bridge._config = None
        reading_bridge._repo = None
        session = reading_bridge.start_reading_session(
            message="讨论这篇",
            excerpt="Eval 只是开始。",
        )
        result = reading_bridge.discuss_reading(
            session_id=session["meta"]["reading_session_id"],
            message="Eval 为什么只是开始？",
            assistant_answer="因为评测只能暴露问题，不能替代产品迭代。",
            evidence=[
                {
                    "source_kind": "original",
                    "title": "原文片段",
                    "excerpt": "Eval 只是开始。",
                    "used_for": "解释 Eval 的边界",
                }
            ],
        )

    assert result["status"] == "success"
    assert "评测只能暴露问题" in result["chat_preview"]
    assert result["source_map"][0]["title"] == "原文片段"

    conn = init_db(reading_config.db_path)
    turn = conn.execute("SELECT * FROM reading_turns").fetchone()
    evidence = conn.execute("SELECT * FROM reading_evidence").fetchone()
    assert turn["user_message"] == "Eval 为什么只是开始？"
    assert evidence["used_for"] == "解释 Eval 的边界"


def test_summarize_reading_session_asks_when_output_type_missing(reading_config):
    from bridge import reading_bridge

    with patch.object(reading_bridge, "load_config", return_value=reading_config):
        reading_bridge._config = None
        reading_bridge._repo = None
        session = reading_bridge.start_reading_session(message="讨论", excerpt="正文")
        result = reading_bridge.summarize_reading_session(
            session_id=session["meta"]["reading_session_id"],
            instruction="整理进笔记",
        )

    assert result["status"] == "success"
    assert result["meta"]["needs_clarification"] is True
    assert "你想整理成哪种" in result["chat_preview"]
    assert "原文增强版主笔记" in result["chat_preview"]
    assert "局部学习笔记" in result["chat_preview"]
    assert "知识卡片" in result["chat_preview"]
    assert "讨论札记" not in result["chat_preview"]
    assert result["meta"]["choices"] == [
        "enhance_capture",
        "local_learning_note",
        "knowledge_card",
    ]


def test_reading_output_type_normalizes_knowledge_card_choices(reading_config):
    from bridge import reading_bridge

    assert reading_bridge._normalize_output_type("3") == "knowledge_card"
    assert reading_bridge._normalize_output_type("知识卡片") == "knowledge_card"
    assert reading_bridge._normalize_output_type("做成卡片") == "knowledge_card"
    assert reading_bridge._normalize_output_type("整理成知识卡片") == "knowledge_card"
    assert reading_bridge._normalize_output_type("knowledge_card") == "knowledge_card"
    assert reading_bridge._normalize_output_type("discussion_note") == "local_learning_note"


def test_summarize_reading_session_creates_learning_note_draft(reading_config):
    from bridge import reading_bridge

    with patch.object(reading_bridge, "load_config", return_value=reading_config):
        reading_bridge._config = None
        reading_bridge._repo = None
        session = reading_bridge.start_reading_session(
            message="讨论",
            source_url="https://example.com/article",
        )
        reading_bridge.discuss_reading(
            session_id=session["meta"]["reading_session_id"],
            message="这段什么意思？",
            assistant_answer="它在说评测之后还需要数据飞轮。",
        )
        result = reading_bridge.summarize_reading_session(
            session_id=session["meta"]["reading_session_id"],
            output_type="local_learning_note",
            instruction="只整理这个概念",
        )

    assert result["status"] == "success"
    assert result["durable_artifacts"][0]["type"] == "reading_draft"
    assert result["meta"]["output_type"] == "local_learning_note"
    assert "已生成局部学习笔记草稿" in result["chat_preview"]


def test_summarize_reading_session_creates_knowledge_card_draft(reading_config):
    from bridge import reading_bridge

    with patch.object(reading_bridge, "load_config", return_value=reading_config):
        reading_bridge._config = None
        reading_bridge._repo = None
        session = reading_bridge.start_reading_session(
            message="讨论",
            source_url="https://example.com/article",
        )
        reading_bridge.discuss_reading(
            session_id=session["meta"]["reading_session_id"],
            message="这个方法以后怎么复用？",
            assistant_answer="它可以作为长文整理前先判断结构的工作流。",
            evidence=[
                {
                    "source_kind": "original",
                    "title": "原文",
                    "excerpt": "先判断结构，再决定整理粒度。",
                }
            ],
        )
        result = reading_bridge.summarize_reading_session(
            session_id=session["meta"]["reading_session_id"],
            output_type="knowledge_card",
            instruction="做成卡片",
        )

    assert result["status"] == "success"
    assert result["durable_artifacts"][0]["type"] == "reading_draft"
    assert result["meta"]["output_type"] == "knowledge_card"
    assert "知识卡片草稿" in result["chat_preview"]

    conn = init_db(reading_config.db_path)
    draft = conn.execute("SELECT * FROM reading_drafts").fetchone()
    assert draft["output_type"] == "knowledge_card"
    assert "这个方法以后怎么复用" in draft["draft_text"]
    assert "长文整理前先判断结构" in draft["draft_text"]
    assert "原文" in draft["draft_text"]


def test_commit_learning_note_writes_independent_file(reading_config):
    from bridge import reading_bridge

    with patch.object(reading_bridge, "load_config", return_value=reading_config):
        with patch.object(
            reading_bridge,
            "_postprocess_committed_learning_note",
            lambda *_args, **_kwargs: {"embedding_index": {"updated": 1}},
        ):
            reading_bridge._config = None
            reading_bridge._repo = None
            session = reading_bridge.start_reading_session(message="讨论", excerpt="正文")
            draft = reading_bridge.summarize_reading_session(
                session_id=session["meta"]["reading_session_id"],
                output_type="local_learning_note",
            )
            result = reading_bridge.commit_reading_artifact(
                draft["meta"]["reading_draft_id"]
            )

    assert result["status"] == "success"
    artifact = result["durable_artifacts"][0]
    assert artifact["type"] == "learning_note"
    assert artifact["path"].startswith("learning/")
    assert (Path(reading_config.vault_path) / artifact["path"]).exists()
    assert result["meta"]["library_postprocess"]["embedding_index"]["updated"] == 1

    conn = init_db(reading_config.db_path)
    capture = conn.execute(
        "SELECT * FROM captures WHERE id = ?",
        (result["meta"]["capture_id"],),
    ).fetchone()
    assert capture is not None
    assert capture["file_path"] == artifact["path"]

    text = (Path(reading_config.vault_path) / artifact["path"]).read_text(encoding="utf-8")
    assert "type: learning_note" in text
    assert f"id: {result['meta']['capture_id']}" in text
    assert "reading_draft_id:" in text


def test_commit_knowledge_card_writes_garden_file(reading_config):
    from bridge import reading_bridge

    with patch.object(reading_bridge, "load_config", return_value=reading_config):
        reading_bridge._config = None
        reading_bridge._repo = None
        session = reading_bridge.start_reading_session(
            message="讨论",
            excerpt="先判断结构，再决定整理粒度。",
        )
        reading_bridge.discuss_reading(
            session_id=session["meta"]["reading_session_id"],
            message="这个方法以后怎么复用？",
            assistant_answer="它可以作为长文整理前先判断结构的工作流。",
        )
        draft = reading_bridge.summarize_reading_session(
            session_id=session["meta"]["reading_session_id"],
            output_type="knowledge_card",
        )
        result = reading_bridge.commit_reading_artifact(
            draft["meta"]["reading_draft_id"]
        )

    assert result["status"] == "success"
    artifact = result["durable_artifacts"][0]
    assert artifact["type"] == "knowledge_card"
    assert result["meta"]["garden_card_id"] == artifact["id"]
    assert artifact["path"].startswith("garden/")
    card_path = Path(reading_config.vault_path) / artifact["path"]
    assert card_path.exists()
    text = card_path.read_text(encoding="utf-8")
    assert "type: garden_card" in text
    assert f"garden_card_id: {artifact['id']}" in text
    assert "card_kind: viewpoint" in text
    assert "它可以作为长文整理前先判断结构的工作流" in text

    conn = init_db(reading_config.db_path)
    row = conn.execute(
        "SELECT * FROM garden_cards WHERE id = ?",
        (artifact["id"],),
    ).fetchone()
    assert row is not None
    assert row["title"].endswith("知识卡片")
    assert row["card_kind"] == "viewpoint"


def test_commit_enhanced_capture_backs_up_and_rewrites_capture(reading_config):
    from bridge import reading_bridge

    vault = Path(reading_config.vault_path)
    captures_dir = vault / "captures"
    captures_dir.mkdir()
    capture_path = captures_dir / "source.md"
    capture_path.write_text(
        "---\nid: cap1\ntitle: 原文章\n---\n\n# 原文章\n\n原正文\n",
        encoding="utf-8",
    )
    conn = init_db(reading_config.db_path)
    conn.execute(
        "INSERT INTO captures (id, file_path, raw_path) VALUES (?, ?, ?)",
        ("cap1", "captures/source.md", "_archive/raw/source.md"),
    )
    conn.commit()
    conn.close()

    with patch.object(reading_bridge, "load_config", return_value=reading_config):
        reading_bridge._config = None
        reading_bridge._repo = None
        session = reading_bridge.start_reading_session(
            message="讨论",
            capture_id="cap1",
        )
        reading_bridge.discuss_reading(
            session_id=session["meta"]["reading_session_id"],
            message="这里为什么重要？",
            assistant_answer="因为它改变了原文论证的理解方式。",
        )
        draft = reading_bridge.summarize_reading_session(
            session_id=session["meta"]["reading_session_id"],
            output_type="enhance_capture",
        )
        result = reading_bridge.commit_reading_artifact(
            draft["meta"]["reading_draft_id"]
        )

    assert result["status"] == "success"
    assert result["durable_artifacts"][0]["type"] == "capture_note"
    assert result["durable_artifacts"][1]["type"] == "capture_backup"
    assert "阅读讨论补充" in capture_path.read_text(encoding="utf-8")
    assert Path(result["durable_artifacts"][1]["path"]).exists()

    conn = init_db(reading_config.db_path)
    backup_count = conn.execute(
        "SELECT count(*) FROM capture_version_backups WHERE capture_id = 'cap1'"
    ).fetchone()[0]
    assert backup_count == 1
