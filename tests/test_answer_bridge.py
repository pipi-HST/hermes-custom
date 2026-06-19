"""Tests for saving Hermes answers as reviewable knowledge drafts."""
from __future__ import annotations

import sqlite3
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from db.schema import init_db


def test_defaults_multi_source_answer_to_learning_note():
    from bridge.answer_bridge import choose_answer_output_type

    sources = [
        {"type": "capture_note", "id": "cap_a", "title": "A"},
        {"type": "capture_note", "id": "cap_b", "title": "B"},
    ]

    decision = choose_answer_output_type(
        instruction="把刚才这段存起来",
        requested_output_type="auto",
        sources=sources,
        target_capture_id="",
    )

    assert decision["output_type"] == "local_learning_note"
    assert decision["target_capture_id"] == ""
    assert decision["needs_target_choice"] is False


def test_requires_explicit_target_for_multi_source_capture_enhancement():
    from bridge.answer_bridge import choose_answer_output_type

    sources = [
        {"type": "capture_note", "id": "cap_a", "title": "A"},
        {"type": "capture_note", "id": "cap_b", "title": "B"},
    ]

    decision = choose_answer_output_type(
        instruction="补进主笔记",
        requested_output_type="enhance_capture",
        sources=sources,
        target_capture_id="",
    )

    assert decision["output_type"] == ""
    assert decision["needs_target_choice"] is True
    assert decision["candidate_captures"] == [
        {"id": "cap_a", "title": "A"},
        {"id": "cap_b", "title": "B"},
    ]


def test_allows_enhance_capture_with_explicit_target():
    from bridge.answer_bridge import choose_answer_output_type

    sources = [
        {"type": "capture_note", "id": "cap_a", "title": "A"},
        {"type": "capture_note", "id": "cap_b", "title": "B"},
    ]

    decision = choose_answer_output_type(
        instruction="补进 A 这篇",
        requested_output_type="enhance_capture",
        sources=sources,
        target_capture_id="cap_a",
    )

    assert decision["output_type"] == "enhance_capture"
    assert decision["target_capture_id"] == "cap_a"
    assert decision["needs_target_choice"] is False


def test_source_capture_candidates_dedupes_and_ignores_empty_ids():
    from bridge.answer_bridge import source_capture_candidates

    sources = [
        {"type": "capture_note", "id": "cap_a", "title": "A"},
        {"type": "knowledge_base", "source_id": "cap_a", "title": "A again"},
        {"type": "web", "id": "", "title": "External"},
    ]

    assert source_capture_candidates(sources) == [{"id": "cap_a", "title": "A"}]


@pytest.fixture
def answer_config(tmp_path):
    db_path = tmp_path / "captures.db"
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    init_db(str(db_path)).close()
    return SimpleNamespace(
        db_path=str(db_path),
        vault_path=str(vault_path),
        llm_api_key="test-key",
        llm_model="test-model",
        llm_base_url="https://example.test",
    )


def test_save_answer_creates_learning_note_draft(answer_config, monkeypatch):
    from bridge import answer_bridge

    monkeypatch.setattr(answer_bridge, "load_config", lambda _path: answer_config)
    answer_bridge._config = None
    answer_bridge._repo = None

    result = answer_bridge.save_answer_to_knowledge(
        hermes_session_id="session-1",
        user_message="这个概念为什么重要？",
        assistant_answer="它重要，因为它把一次性回答变成可复用的判断框架。",
        instruction="把刚才这段存起来",
        output_type="auto",
        assistant_message_id="assistant-1",
        sources=[
            {"type": "capture_note", "id": "cap_a", "title": "材料 A"},
            {"type": "capture_note", "id": "cap_b", "title": "材料 B"},
        ],
    )

    assert result["status"] == "success"
    assert result["meta"]["output_type"] == "local_learning_note"
    draft_id = result["meta"]["reading_draft_id"]

    conn = sqlite3.connect(answer_config.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM reading_drafts WHERE id = ?", (draft_id,)).fetchone()
    assert row["output_type"] == "local_learning_note"
    assert "这个概念为什么重要" in row["draft_text"]
    assert "它重要，因为" in row["draft_text"]
    assert "## 可沉淀的理解" not in row["draft_text"]


def test_saved_answer_commit_uses_reading_write_service(answer_config, monkeypatch):
    from bridge import answer_bridge, reading_bridge

    monkeypatch.setattr(answer_bridge, "load_config", lambda _path: answer_config)
    monkeypatch.setattr(reading_bridge, "load_config", lambda _path: answer_config)
    monkeypatch.setattr(
        reading_bridge,
        "_postprocess_committed_learning_note",
        lambda *_args, **_kwargs: {"embedding_index": {"updated": 1}},
    )
    answer_bridge._config = None
    answer_bridge._repo = None
    reading_bridge._config = None
    reading_bridge._repo = None

    draft = answer_bridge.save_answer_to_knowledge(
        hermes_session_id="session-1",
        user_message="这个框架怎么沉淀？",
        assistant_answer="先保留问题、判断依据和后续动作。",
        instruction="把刚才这段存起来",
        output_type="auto",
        assistant_message_id="assistant-1",
        sources=[{"type": "capture_note", "id": "cap_a", "title": "材料 A"}],
    )

    committed = reading_bridge.commit_reading_artifact(draft["meta"]["reading_draft_id"])

    assert committed["status"] == "success"
    assert committed["meta"]["write_service"] == "knowledge_write_service"
    artifact = committed["durable_artifacts"][0]
    assert artifact["type"] == "learning_note"
    assert (Path(answer_config.vault_path) / artifact["path"]).exists()
    conn = sqlite3.connect(answer_config.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT target_capture_id, status FROM reading_drafts WHERE id = ?",
        (draft["meta"]["reading_draft_id"],),
    ).fetchone()
    assert row["status"] == "committed"
    assert row["target_capture_id"] == committed["meta"]["capture_id"]


def test_save_answer_prefers_latest_full_rag_answer_for_recent_answer(answer_config, monkeypatch):
    from bridge import answer_bridge

    monkeypatch.setattr(answer_bridge, "load_config", lambda _path: answer_config)
    answer_bridge._config = None
    answer_bridge._repo = None

    conn = sqlite3.connect(answer_config.db_path)
    conn.execute(
        """INSERT INTO agent_runs
           (id, agent, intent, trigger, status, input_json, output_json, evidence_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "rag-run-1",
            "rag",
            "search_knowledge_base",
            "knowledge_workbench",
            "success",
            json.dumps({"query": "企业级取数 Agent"}),
            json.dumps(
                {
                    "answer": "完整答案第一段。\n\n完整答案第二段，包含更详细的实施步骤。",
                    "sources": [{"type": "capture_note", "id": "cap1", "title": "数据 Agent 材料"}],
                },
                ensure_ascii=False,
            ),
            "{}",
        ),
    )
    conn.commit()
    conn.close()

    result = answer_bridge.save_answer_to_knowledge(
        hermes_session_id="session-1",
        user_message="企业级取数 Agent 怎么做？",
        assistant_answer="完整答案第一段。（已省略后续内容）",
        instruction="这个回答回答得不错，可以存成笔记",
        output_type="auto",
        assistant_message_id="assistant-1",
        sources=[],
    )

    assert result["status"] == "success"
    assert result["meta"]["answer_source"] == "latest_rag_full_answer"
    draft_id = result["meta"]["reading_draft_id"]

    conn = sqlite3.connect(answer_config.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM reading_drafts WHERE id = ?", (draft_id,)).fetchone()
    assert "完整答案第二段" in row["draft_text"]
    assert "数据 Agent 材料" in row["draft_text"]


def test_save_answer_asks_for_target_when_multiple_captures(answer_config, monkeypatch):
    from bridge import answer_bridge

    monkeypatch.setattr(answer_bridge, "load_config", lambda _path: answer_config)
    answer_bridge._config = None
    answer_bridge._repo = None

    result = answer_bridge.save_answer_to_knowledge(
        hermes_session_id="session-1",
        user_message="补进主笔记",
        assistant_answer="这是跨材料回答。",
        instruction="补进主笔记",
        output_type="enhance_capture",
        sources=[
            {"type": "capture_note", "id": "cap_a", "title": "材料 A"},
            {"type": "capture_note", "id": "cap_b", "title": "材料 B"},
        ],
    )

    assert result["status"] == "success"
    assert result["meta"]["needs_target_choice"] is True
    assert "不能自动判断" in result["chat_preview"]


def test_revise_answer_draft_updates_open_draft(answer_config, monkeypatch):
    from bridge import answer_bridge

    monkeypatch.setattr(answer_bridge, "load_config", lambda _path: answer_config)
    monkeypatch.setattr(
        answer_bridge,
        "_call_revision_llm",
        lambda **_kwargs: "# 修改后文稿\n\n这是按用户意见展开后的完整版本。",
    )
    answer_bridge._config = None
    answer_bridge._repo = None

    created = answer_bridge.save_answer_to_knowledge(
        hermes_session_id="session-1",
        user_message="怎么搭建？",
        assistant_answer="先做最小版本。",
        instruction="存为笔记",
        output_type="auto",
        assistant_message_id="assistant-1",
        sources=[],
    )
    draft_id = created["meta"]["reading_draft_id"]

    revised = answer_bridge.revise_answer_draft(
        draft_id=draft_id,
        instruction="把最小版本展开说清楚",
    )

    assert revised["status"] == "success"
    conn = sqlite3.connect(answer_config.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM reading_drafts WHERE id = ?", (draft_id,)).fetchone()
    assert "修改后文稿" in row["draft_text"]
    assert "展开后的完整版本" in row["draft_text"]
