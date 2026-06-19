from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from agents.theme_agent import create_pending_question
from bridge.context import KnowledgeContext
from db.repo import CaptureRepo
from db.schema import init_db


def _context(tmp_path: Path) -> KnowledgeContext:
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    config = SimpleNamespace(
        db_path=str(db_path),
        vault_path=str(vault_path),
        llm_api_key="test-key",
        llm_provider="openai",
        llm_model="test-model",
        llm_base_url="https://example.test",
        qwen_api_key="",
    )
    return KnowledgeContext(config=config, conn=init_db(str(db_path)), vault_path=vault_path)


def test_reading_bridge_accepts_explicit_context(monkeypatch, tmp_path):
    from bridge import reading_bridge

    context = _context(tmp_path)
    monkeypatch.setattr(reading_bridge, "load_config", lambda _path: (_ for _ in ()).throw(AssertionError("default config used")))

    result = reading_bridge.start_reading_session(
        message="讨论这段材料",
        excerpt="这是一段需要阅读讨论的材料。",
        context=context,
    )

    assert result["status"] == "success"
    session_id = result["meta"]["reading_session_id"]
    assert context.conn.execute("SELECT id FROM reading_sessions WHERE id = ?", (session_id,)).fetchone()


def test_capture_bridge_accepts_explicit_context(monkeypatch, tmp_path):
    from bridge import capture_bridge

    context = _context(tmp_path)
    draft = context.capture_agent().draft_repo.create(
        raw_text="原文",
        draft_text="草稿正文",
        title="上下文草稿",
        status="draft_ready",
    )
    monkeypatch.setattr(capture_bridge, "load_config", lambda _path: (_ for _ in ()).throw(AssertionError("default config used")))

    result = capture_bridge.show_capture_draft(draft["id"], context=context)

    assert result["status"] == "success"
    assert result["meta"]["draft_id"] == draft["id"]
    assert "草稿正文" in result["chat_preview"]


def test_garden_bridge_accepts_explicit_context(monkeypatch, tmp_path):
    from bridge import garden_bridge

    context = _context(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    context.conn.execute(
        """INSERT INTO garden_cards
           (id, title, one_liner, core_arguments, knowledge_links, key_materials,
            my_view, status, capture_ids, created_at, updated_at, review_interval)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "card1",
            "上下文卡片",
            "一句话",
            "[]",
            "[]",
            "[]",
            "",
            "seed",
            "[]",
            now,
            now,
            "1d",
        ),
    )
    context.conn.commit()
    monkeypatch.setattr(garden_bridge, "load_config", lambda _path: (_ for _ in ()).throw(AssertionError("default config used")))

    result = garden_bridge.archive_garden_card("card1", context=context)

    assert result["status"] == "success"
    row = context.conn.execute("SELECT status FROM garden_cards WHERE id = 'card1'").fetchone()
    assert row["status"] == "archived"


def test_theme_bridge_accepts_explicit_context(monkeypatch, tmp_path):
    from bridge import theme_bridge

    context = _context(tmp_path)
    repo = CaptureRepo(context.conn)
    first = create_pending_question(
        repo,
        "theme",
        "上下文候选组可以沉淀什么卡片？",
        context_ref="theme-context",
        related_capture_ids=["cap-a", "cap-b"],
    )
    monkeypatch.setattr(theme_bridge, "load_config", lambda _path: (_ for _ in ()).throw(AssertionError("default config used")))

    result = theme_bridge.list_theme_candidates(limit=5, context=context)

    assert result["status"] == "success"
    assert result["next_actions"][0]["payload"]["question_id"] == first["id"]
