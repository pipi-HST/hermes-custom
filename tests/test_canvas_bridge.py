from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bridge.canvas_bridge import generate_canvas
from db.schema import init_db


def _write_note(vault: Path, rel_path: str, title: str, body: str = "正文") -> None:
    path = vault / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: {title}\n---\n\n{body}\n", encoding="utf-8")


def _insert_capture(conn, capture_id: str, file_path: str) -> None:
    conn.execute(
        "INSERT INTO captures (id, file_path, raw_path) VALUES (?, ?, '')",
        (capture_id, file_path),
    )
    conn.commit()


def _insert_garden_card(
    conn,
    vault: Path,
    card_id: str,
    title: str,
    file_path: str,
    capture_ids: list[str],
    *,
    kind: str = "method",
) -> None:
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, one_liner, card_kind, core_arguments, knowledge_links,
            key_materials, status, capture_ids)
           VALUES (?, ?, ?, ?, '[]', '[]', '[]', 'seed', ?)""",
        (
            card_id,
            title,
            "一句话",
            kind,
            json.dumps(capture_ids, ensure_ascii=False),
        ),
    )
    _write_note(vault, file_path, title)
    conn.commit()


def _setup_db_and_vault(tmp_path):
    db_path = tmp_path / "captures.db"
    vault = tmp_path / "vault"
    conn = init_db(str(db_path))
    return conn, db_path, vault


def test_generate_canvas_from_theme_writes_obsidian_canvas(tmp_path):
    conn, db_path, vault = _setup_db_and_vault(tmp_path)
    _write_note(
        vault,
        "themes/ai-agent.md",
        "AI Agent",
        body="- [[Building Effective AI Agents]]\n",
    )
    _write_note(vault, "captures/effective.md", "Building Effective AI Agents")
    _insert_capture(conn, "cap1", "captures/effective.md")
    _insert_garden_card(
        conn,
        vault,
        "card1",
        "AI Agent 工程化方法卡",
        "garden/AI Agent 工程化方法卡-card1.md",
        ["cap1"],
    )

    envelope = generate_canvas(
        theme="AI Agent",
        db_path=str(db_path),
        vault_path=str(vault),
    )

    canvas_path = vault / "canvas" / "AI Agent-关系图.canvas"
    assert envelope["status"] == "success"
    assert canvas_path.exists()
    payload = json.loads(canvas_path.read_text(encoding="utf-8"))
    assert any(node.get("file") == "themes/ai-agent.md" for node in payload["nodes"])
    assert any(node.get("file") == "captures/effective.md" for node in payload["nodes"])
    assert any(node.get("file") == "garden/AI Agent 工程化方法卡-card1.md" for node in payload["nodes"])
    assert "已生成 Obsidian Canvas" in envelope["chat_preview"]
    assert envelope["durable_artifacts"][0]["type"] == "obsidian_canvas"


def test_generate_canvas_matches_theme_filename_with_hyphenated_words(tmp_path):
    conn, db_path, vault = _setup_db_and_vault(tmp_path)
    _write_note(vault, "themes/ai-agent.md", "ai-agent")
    _write_note(vault, "captures/effective.md", "Building Effective AI Agents")
    _insert_capture(conn, "cap1", "captures/effective.md")

    envelope = generate_canvas(
        theme="AI Agent",
        capture_ids=["cap1"],
        db_path=str(db_path),
        vault_path=str(vault),
    )

    assert envelope["status"] == "success"
    assert (vault / "canvas" / "ai-agent-关系图.canvas").exists()


def test_generate_canvas_uses_moc_wiki_links_to_limit_sources(tmp_path):
    conn, db_path, vault = _setup_db_and_vault(tmp_path)
    _write_note(
        vault,
        "themes/ai-agent.md",
        "AI Agent",
        body="- [[Building Effective AI Agents]]\n",
    )
    _write_note(vault, "captures/effective.md", "Building Effective AI Agents")
    _write_note(vault, "captures/unrelated.md", "不相关笔记")
    _insert_capture(conn, "cap1", "captures/effective.md")
    _insert_capture(conn, "cap2", "captures/unrelated.md")
    _insert_garden_card(
        conn,
        vault,
        "card1",
        "AI Agent 工程化方法卡",
        "garden/AI Agent 工程化方法卡-card1.md",
        ["cap1"],
    )
    _insert_garden_card(
        conn,
        vault,
        "card2",
        "不相关卡片",
        "garden/不相关卡片-card2.md",
        ["cap2"],
    )

    envelope = generate_canvas(
        theme="AI Agent",
        db_path=str(db_path),
        vault_path=str(vault),
    )

    payload = json.loads((vault / "canvas" / "AI Agent-关系图.canvas").read_text(encoding="utf-8"))
    files = {node.get("file") for node in payload["nodes"] if node.get("type") == "file"}
    assert envelope["status"] == "success"
    assert "captures/effective.md" in files
    assert "garden/AI Agent 工程化方法卡-card1.md" in files
    assert "captures/unrelated.md" not in files
    assert "garden/不相关卡片-card2.md" not in files


def test_generate_canvas_from_pending_theme_candidate_uses_related_captures(tmp_path):
    conn, db_path, vault = _setup_db_and_vault(tmp_path)
    _write_note(vault, "themes/ai-agent.md", "AI Agent")
    _write_note(vault, "captures/effective.md", "Building Effective AI Agents")
    _insert_capture(conn, "cap1", "captures/effective.md")
    expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    conn.execute(
        """INSERT INTO pending_questions
           (id, agent_type, question_text, context_ref, related_capture_ids, expires_at)
           VALUES (?, 'theme', ?, 'theme-ai-agent', ?, ?)""",
        ("q1", "AI Agent 这些材料有什么联系？", json.dumps(["cap1"]), expires),
    )
    conn.commit()

    envelope = generate_canvas(
        theme="AI Agent",
        question_id="q1",
        db_path=str(db_path),
        vault_path=str(vault),
    )

    payload = json.loads((vault / "canvas" / "AI Agent-关系图.canvas").read_text(encoding="utf-8"))
    assert envelope["status"] == "success"
    assert any(node.get("file") == "captures/effective.md" for node in payload["nodes"])
    assert envelope["meta"]["capture_count"] == 1


def test_generate_canvas_without_garden_cards_still_creates_capture_theme_canvas(tmp_path):
    conn, db_path, vault = _setup_db_and_vault(tmp_path)
    _write_note(vault, "themes/ai-agent.md", "AI Agent")
    _write_note(vault, "captures/effective.md", "Building Effective AI Agents")
    _insert_capture(conn, "cap1", "captures/effective.md")

    envelope = generate_canvas(
        theme="AI Agent",
        capture_ids=["cap1"],
        db_path=str(db_path),
        vault_path=str(vault),
    )

    assert envelope["status"] == "success"
    assert "暂时没有找到关联的 Garden 卡" in envelope["warnings"]
    assert (vault / "canvas" / "AI Agent-关系图.canvas").exists()


def test_generate_canvas_does_not_overwrite_non_hermes_canvas(tmp_path):
    conn, db_path, vault = _setup_db_and_vault(tmp_path)
    _write_note(vault, "themes/ai-agent.md", "AI Agent")
    _write_note(vault, "captures/effective.md", "Building Effective AI Agents")
    _insert_capture(conn, "cap1", "captures/effective.md")
    canvas_dir = vault / "canvas"
    canvas_dir.mkdir(parents=True)
    original = canvas_dir / "AI Agent-关系图.canvas"
    original.write_text('{"nodes":[],"edges":[]}', encoding="utf-8")

    envelope = generate_canvas(
        theme="AI Agent",
        capture_ids=["cap1"],
        db_path=str(db_path),
        vault_path=str(vault),
    )

    assert envelope["status"] == "success"
    assert original.read_text(encoding="utf-8") == '{"nodes":[],"edges":[]}'
    assert (canvas_dir / "AI Agent-关系图-Hermes.canvas").exists()
