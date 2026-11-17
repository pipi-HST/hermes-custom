import json

from agents.rag_context import build_rag_context_pack
from db.schema import init_db


def test_context_pack_adds_garden_cards_related_to_capture_hits(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, ?)",
        ("cap1", "captures/A.md", "2026-05-31T00:00:00+00:00"),
    )
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, payload_json, status, capture_ids, created_at, updated_at, review_interval)
           VALUES (?, ?, ?, 'seed', ?, ?, ?, '1d')""",
        (
            "g1",
            "AI 工程化",
            '{"schema_version":"garden_card.v2","claim":"AI 的价值来自工程流程。","card_kind":"viewpoint","core_judgments":[{"id":"j1","statement":"规则、工具和检查共同提升可靠性。","reasoning":"","support":[]}],"source_ids":["cap1"]}',
            json.dumps(["cap1"], ensure_ascii=False),
            "2026-05-31T00:00:00+00:00",
            "2026-05-31T00:00:00+00:00",
        ),
    )

    pack = build_rag_context_pack(
        conn,
        capture_hits=[{"id": "cap1", "title": "材料 A", "file_path": "captures/A.md"}],
        vault_path=str(tmp_path),
    )

    assert pack["capture_count"] == 1
    assert pack["garden_count"] == 1
    assert pack["garden_cards"][0]["title"] == "AI 工程化"
    assert "工程流程" in pack["garden_cards"][0]["one_liner"]


def test_context_pack_deduplicates_garden_cards(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    for capture_id in ("cap1", "cap2"):
        conn.execute(
            "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, ?)",
            (capture_id, f"captures/{capture_id}.md", "2026-05-31T00:00:00+00:00"),
        )
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, payload_json, status, capture_ids, created_at, updated_at, review_interval)
           VALUES (?, ?, ?, 'seed', ?, ?, ?, '1d')""",
        (
            "g1",
            "同一张卡",
            '{"schema_version":"garden_card.v2","claim":"同一张卡只出现一次。","card_kind":"viewpoint","source_ids":["cap1","cap2"]}',
            json.dumps(["cap1", "cap2"], ensure_ascii=False),
            "2026-05-31T00:00:00+00:00",
            "2026-05-31T00:00:00+00:00",
        ),
    )

    pack = build_rag_context_pack(
        conn,
        capture_hits=[
            {"id": "cap1", "title": "材料 1", "file_path": "captures/cap1.md"},
            {"id": "cap2", "title": "材料 2", "file_path": "captures/cap2.md"},
        ],
        vault_path=str(tmp_path),
    )

    assert pack["garden_count"] == 1


def test_context_pack_reads_theme_links_from_capture_note(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    note_path = tmp_path / "captures" / "A.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text(
        """---
id: cap1
title: 材料 A
linked_themes:
  - AI Agent
---

## 知识关联
- [[themes/AI Agent|AI Agent]]
""",
        encoding="utf-8",
    )

    pack = build_rag_context_pack(
        conn,
        capture_hits=[{"id": "cap1", "title": "材料 A", "file_path": "captures/A.md"}],
        vault_path=str(tmp_path),
    )

    assert pack["theme_count"] == 1
    assert pack["theme_pages"][0]["title"] == "AI Agent"


def test_context_pack_accepts_graph_nodes(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    graph = {
        "expanded_garden_cards": [
            {
                "id": "g1",
                "title": "AI Agent 工程化",
                "one_liner": "用工程手段管理 Agent。",
                "core_arguments": [],
                "capture_ids": ["cap1"],
            }
        ],
        "expanded_theme_groups": [
            {
                "id": "t1",
                "title": "Agent 复杂度控制",
                "capture_ids": ["cap1", "cap2"],
                "status": "pending",
            }
        ],
        "expanded_theme_pages": [
            {"title": "Agent 方法论", "path": "themes/Agent 方法论.md"}
        ],
        "trace": {"seed_count": 1, "final_count": 1},
    }

    pack = build_rag_context_pack(
        conn,
        capture_hits=[{"id": "cap1", "title": "材料 A", "file_path": "captures/A.md"}],
        vault_path=str(tmp_path),
        graph=graph,
    )

    assert pack["garden_cards"][0]["title"] == "AI Agent 工程化"
    assert pack["theme_groups"][0]["title"] == "Agent 复杂度控制"
    assert pack["graph_trace"]["seed_count"] == 1
