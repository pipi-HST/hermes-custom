"""Tests for Reading Session bridge."""
from __future__ import annotations

import hashlib
import sqlite3
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


def test_reading_metadata_llm_requires_explicit_env(reading_config, monkeypatch):
    from bridge import reading_bridge

    reading_config.llm_api_key = "real-key"
    reading_config.llm_base_url = "https://llm.example"
    monkeypatch.delenv("HERMES_FULL_TEXT_METADATA_LLM", raising=False)
    assert reading_bridge._metadata_llm_enabled(reading_config) is False

    monkeypatch.setenv("HERMES_FULL_TEXT_METADATA_LLM", "1")
    assert reading_bridge._metadata_llm_enabled(reading_config) is True


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
    assert "资料笔记" in result["chat_preview"]
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
    assert "已生成资料笔记草稿" in result["chat_preview"]


def test_summarize_reading_session_metadata_uses_full_draft_text(reading_config):
    from bridge import reading_bridge

    captured: dict[str, str] = {}

    def fake_metadata(_config, markdown: str) -> dict:
        captured["markdown"] = markdown
        return {
            "summary": "阅读全文后的讨论摘要。",
            "tags": ["ai-eval", "product-loop"],
            "source": "ai_full_text",
        }

    with patch.object(reading_bridge, "load_config", return_value=reading_config):
        with patch.object(reading_bridge, "_generate_full_text_metadata", fake_metadata):
            with patch.object(
                reading_bridge,
                "_postprocess_committed_learning_note",
                lambda *_args, **_kwargs: {"embedding_index": {"updated": 1}},
            ):
                reading_bridge._config = None
                reading_bridge._repo = None
                session = reading_bridge.start_reading_session(message="讨论", excerpt="正文")
                reading_bridge.discuss_reading(
                    session_id=session["meta"]["reading_session_id"],
                    message="这个方法怎么理解？",
                    assistant_answer="开头解释。\n\n" + ("中段分析。\n" * 20) + "\nTAIL_ONLY_SIGNAL：尾部判断。",
                )
                draft = reading_bridge.summarize_reading_session(
                    session_id=session["meta"]["reading_session_id"],
                    output_type="local_learning_note",
                )
                result = reading_bridge.commit_reading_artifact(draft["meta"]["reading_draft_id"], confirmation="确认整理")

    assert "TAIL_ONLY_SIGNAL" in captured["markdown"]
    text = (Path(reading_config.vault_path) / result["durable_artifacts"][0]["path"]).read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]
    assert "story: 阅读全文后的讨论摘要。" in frontmatter
    assert "ai-eval" in frontmatter
    assert "product-loop" in frontmatter


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


def test_commit_learning_note_writes_capture_note_file(reading_config):
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
            result = reading_bridge.commit_reading_artifact(draft["meta"]["reading_draft_id"], confirmation="确认整理")

    assert result["status"] == "success"
    artifact = result["durable_artifacts"][0]
    assert artifact["type"] == "capture_note"
    assert artifact["path"].startswith("captures/")
    assert (Path(reading_config.vault_path) / artifact["path"]).exists()
    assert result["meta"]["library_postprocess"]["embedding_index"]["updated"] == 1
    assert result["meta"]["write_service"] == "knowledge_write_service"
    assert result["meta"]["source_output_type"] == "local_learning_note"

    conn = init_db(reading_config.db_path)
    capture = conn.execute(
        "SELECT * FROM captures WHERE id = ?",
        (result["meta"]["capture_id"],),
    ).fetchone()
    assert capture is not None
    assert capture["file_path"] == artifact["path"]

    text = (Path(reading_config.vault_path) / artifact["path"]).read_text(encoding="utf-8")
    assert "type: capture_note" in text
    capture_id = result["meta"]["capture_id"]
    # YAML quotes all-digit ids to preserve them as strings.
    assert f"id: {capture_id}" in text or f"id: '{capture_id}'" in text
    assert "reading_draft_id:" in text


def test_discard_reading_artifact_marks_draft_discarded(reading_config):
    from bridge import reading_bridge

    with patch.object(reading_bridge, "load_config", return_value=reading_config):
        reading_bridge._config = None
        reading_bridge._repo = None
        session = reading_bridge.start_reading_session(message="讨论", excerpt="正文")
        draft = reading_bridge.summarize_reading_session(
            session_id=session["meta"]["reading_session_id"],
            output_type="local_learning_note",
        )
        draft_id = draft["meta"]["reading_draft_id"]
        result = reading_bridge.discard_reading_artifact(draft_id)

    assert result["status"] == "success"
    assert result["intent"] == "discard_reading_artifact"
    assert result["meta"]["draft_status"] == "discarded"

    conn = init_db(reading_config.db_path)
    row = conn.execute("SELECT status FROM reading_drafts WHERE id = ?", (draft_id,)).fetchone()
    assert row["status"] == "discarded"


def test_commit_learning_note_warns_when_library_postprocess_fails(reading_config):
    from bridge import reading_bridge

    with patch.object(reading_bridge, "load_config", return_value=reading_config):
        with patch.object(
            reading_bridge,
            "_postprocess_committed_learning_note",
            lambda *_args, **_kwargs: {"error": "outliner timeout"},
        ):
            reading_bridge._config = None
            reading_bridge._repo = None
            session = reading_bridge.start_reading_session(message="讨论", excerpt="正文")
            draft = reading_bridge.summarize_reading_session(
                session_id=session["meta"]["reading_session_id"],
                output_type="local_learning_note",
            )
            result = reading_bridge.commit_reading_artifact(draft["meta"]["reading_draft_id"], confirmation="确认整理")

    assert result["status"] == "success"
    assert "已加入知识库检索索引" not in result["chat_preview"]
    assert "索引维护失败" in result["chat_preview"]
    assert result["warnings"] == ["Library postprocess failed: outliner timeout"]


def test_commit_knowledge_card_creates_structured_card_without_markdown(reading_config):
    from bridge import reading_bridge

    with patch.object(reading_bridge, "load_config", return_value=reading_config):
        with patch.object(reading_bridge.httpx, "Client", side_effect=RuntimeError("no model")):
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
            result = reading_bridge.commit_reading_artifact(draft["meta"]["reading_draft_id"], confirmation="确认整理")

    assert result["status"] == "success"
    artifact = result["durable_artifacts"][0]
    assert artifact["type"] == "knowledge_card"
    assert result["meta"]["garden_card_id"] == artifact["id"]
    assert artifact.get("path", "") == ""
    assert result["meta"]["storage"] == "sqlite"
    assert result["meta"]["file_path"] == ""
    assert not (Path(reading_config.vault_path) / "garden").exists()

    conn = init_db(reading_config.db_path)
    row = conn.execute(
        "SELECT * FROM garden_cards WHERE id = ?",
        (artifact["id"],),
    ).fetchone()
    assert row is not None
    assert row["title"].endswith("知识卡片")
    assert row["card_kind"] == "viewpoint"


def test_synthesize_reading_card_warns_on_llm_failure(reading_config):
    from bridge import reading_bridge

    with patch.object(reading_bridge.httpx, "Client", side_effect=RuntimeError("no model")):
        garden_data, card_body, warnings = reading_bridge._synthesize_reading_card(
            reading_config,
            title="复用方法",
            draft_text="这个方法以后可以复用。",
            summary="先判断结构，再决定整理粒度。",
            session={"focus": "长文整理"},
            evidence=[],
        )

    assert garden_data["card_kind"] == "viewpoint"
    assert "先判断结构" in card_body
    assert warnings == ["Reading card LLM synthesis failed: no model"]


@pytest.fixture
def enhancement_source(reading_config):
    vault = Path(reading_config.vault_path)
    captures_dir = vault / "captures"
    captures_dir.mkdir()
    capture_path = captures_dir / "source.md"
    capture_path.write_bytes(
        "---\r\nid: cap1\r\ntitle: 原文章\r\n---\r\n\r\n# 原文章\r\n\r\n原正文\r\n".encode("utf-8"),
    )
    conn = init_db(reading_config.db_path)
    conn.execute(
        "INSERT INTO captures (id, file_path, raw_path) VALUES (?, ?, ?)",
        ("cap1", "captures/source.md", "_archive/raw/source.md"),
    )
    conn.commit()
    conn.close()
    return capture_path


def _new_enhancement_draft(reading_config, monkeypatch):
    from bridge import reading_bridge

    monkeypatch.setattr(reading_bridge, "load_config", lambda _path: reading_config)
    monkeypatch.setattr(reading_bridge, "_config", None)
    monkeypatch.setattr(reading_bridge, "_repo", None)
    session = reading_bridge.start_reading_session(message="讨论", capture_id="cap1")
    reading_bridge.discuss_reading(
        session_id=session["meta"]["reading_session_id"],
        message="这里为什么重要？",
        assistant_answer="因为它改变了原文论证的理解方式。",
    )
    draft = reading_bridge.summarize_reading_session(
        session_id=session["meta"]["reading_session_id"],
        output_type="enhance_capture",
    )
    assert draft["status"] == "success"
    return draft["meta"]["reading_draft_id"]


def test_commit_enhanced_capture_backs_up_and_rewrites_capture(reading_config, enhancement_source, monkeypatch):
    from bridge import atomic_io, reading_bridge

    capture_path = enhancement_source
    vault = Path(reading_config.vault_path)
    original = capture_path.read_bytes()
    draft_id = _new_enhancement_draft(reading_config, monkeypatch)
    real_replace = atomic_io.os.replace

    def check_backup_then_replace(source, destination):
        if Path(destination) == capture_path:
            # An independent connection must see the row before the note changes.
            with sqlite3.connect(reading_config.db_path) as conn:
                backup_path, previous_hash, new_hash = conn.execute(
                    """SELECT backup_file_path, previous_file_hash, new_file_hash
                       FROM capture_version_backups WHERE reading_draft_id = ?""",
                    (draft_id,),
                ).fetchone()
            assert capture_path.read_bytes() == original
            assert (vault / backup_path).read_bytes() == original
            assert previous_hash == hashlib.sha256(original).hexdigest()
            assert new_hash == hashlib.sha256(Path(source).read_bytes()).hexdigest()
        return real_replace(source, destination)

    monkeypatch.setattr(atomic_io.os, "replace", check_backup_then_replace)
    result = reading_bridge.commit_reading_artifact(draft_id, confirmation="确认整理")

    assert result["status"] == "success"
    assert result["durable_artifacts"][0]["type"] == "capture_note"
    assert result["durable_artifacts"][1]["type"] == "capture_backup"
    assert "阅读讨论补充" in capture_path.read_text(encoding="utf-8")
    backup_path = result["durable_artifacts"][1]["path"]
    assert backup_path.startswith("_archive/write_backups/captures/source-")
    assert (vault / backup_path).exists()
    assert result["meta"]["write_service"] == "knowledge_write_service"

    conn = init_db(reading_config.db_path)
    backup = conn.execute(
        "SELECT backup_file_path FROM capture_version_backups WHERE capture_id = 'cap1'"
    ).fetchone()
    assert backup["backup_file_path"] == backup_path


@pytest.mark.parametrize("change", ["body", "frontmatter", "legacy"])
def test_enhancement_rejects_unverified_snapshot(reading_config, enhancement_source, monkeypatch, change):
    from bridge import reading_bridge

    draft_id = _new_enhancement_draft(reading_config, monkeypatch)
    repo = reading_bridge._repo
    current = enhancement_source.read_bytes()
    if change == "legacy":
        repo.update_draft(draft_id, meta_json={"instruction": "legacy draft"})
    elif change == "body":
        current += "\r\n保存后另外编辑的正文。\r\n".encode("utf-8")
    else:
        current = current.replace(b"id: cap1\r\n", b"id: cap1\r\nfolder_path: New Topic\r\n")
    enhancement_source.write_bytes(current)

    result = reading_bridge.commit_reading_artifact(draft_id, confirmation="确认整理")

    assert result["status"] == "error"
    assert "重新生成" in result["errors"][0]
    assert enhancement_source.read_bytes() == current
    draft = repo.get_draft(draft_id)
    assert draft["status"] == "open"
    assert repo.get_session(draft["session_id"])["status"] == "summarized"
    assert repo.conn.execute("SELECT count(*) FROM capture_version_backups").fetchone()[0] == 0


def test_enhancement_leaves_note_intact_when_backup_registration_fails(
    reading_config, enhancement_source, monkeypatch,
):
    from bridge import reading_bridge

    draft_id = _new_enhancement_draft(reading_config, monkeypatch)
    original = enhancement_source.read_bytes()
    repo = reading_bridge._repo
    repo.conn.executescript("""
        CREATE TRIGGER reject_backup BEFORE INSERT ON capture_version_backups
        BEGIN SELECT RAISE(ABORT, 'backup registration failed'); END;
    """)

    with pytest.raises(sqlite3.IntegrityError, match="backup registration failed"):
        reading_bridge.commit_reading_artifact(draft_id, confirmation="确认整理")

    assert enhancement_source.read_bytes() == original
    assert repo.get_draft(draft_id)["status"] == "open"


def test_enhancement_keeps_registered_backup_when_replace_fails(
    reading_config, enhancement_source, monkeypatch,
):
    from bridge import atomic_io, reading_bridge

    draft_id = _new_enhancement_draft(reading_config, monkeypatch)
    original = enhancement_source.read_bytes()
    real_replace = atomic_io.os.replace

    def fail_note_replace(source, destination):
        if Path(destination) == enhancement_source:
            raise OSError("note replace failed")
        return real_replace(source, destination)

    monkeypatch.setattr(atomic_io.os, "replace", fail_note_replace)
    with pytest.raises(OSError, match="note replace failed"):
        reading_bridge.commit_reading_artifact(draft_id, confirmation="确认整理")

    repo = reading_bridge._repo
    backup_path = repo.conn.execute(
        "SELECT backup_file_path FROM capture_version_backups WHERE reading_draft_id = ?", (draft_id,),
    ).fetchone()[0]
    assert (Path(reading_config.vault_path) / backup_path).read_bytes() == original
    assert enhancement_source.read_bytes() == original
    assert repo.get_draft(draft_id)["status"] == "open"


@pytest.mark.parametrize("edited", [False, True])
def test_answer_enhancement_includes_and_checks_original_body(
    reading_config, enhancement_source, monkeypatch, edited,
):
    from bridge import answer_bridge, reading_bridge
    from bridge.context import KnowledgeContext

    monkeypatch.setattr(answer_bridge, "load_config", lambda _path: reading_config)
    monkeypatch.setattr(answer_bridge, "_config", None)
    monkeypatch.setattr(answer_bridge, "_repo", None)
    result = answer_bridge.save_answer_to_knowledge(
        assistant_answer="来自回答的补充。",
        output_type="enhance_capture",
        target_capture_id="cap1",
        answer_source="provided",
    )

    assert result["status"] == "success"
    draft_id = result["meta"]["reading_draft_id"]
    repo = answer_bridge._repo
    draft = repo.get_draft(draft_id)
    if edited:
        current = enhancement_source.read_bytes() + "\r\n另外编辑的正文。\r\n".encode("utf-8")
        enhancement_source.write_bytes(current)
    commit = reading_bridge.commit_reading_artifact(
        draft_id,
        confirmation="确认整理",
        context=KnowledgeContext(reading_config, repo.conn, Path(reading_config.vault_path)),
    )

    assert "原正文" in draft["draft_text"]
    if edited:
        assert commit["status"] == "error"
        assert enhancement_source.read_bytes() == current
        assert repo.get_draft(draft_id)["status"] == "open"
    else:
        assert commit["status"] == "success"
        note = enhancement_source.read_text(encoding="utf-8")
        assert "原正文" in note
        assert "来自回答的补充" in note
