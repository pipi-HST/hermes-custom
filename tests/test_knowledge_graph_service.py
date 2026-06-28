from __future__ import annotations

import json
import struct

from agents.embedder import DIM
from agents.knowledge_graph import KnowledgeGraphService
from db.embedding_repo import CaptureEmbeddingRepo
from db.schema import init_db


def _emb(seed: float) -> bytes:
    vec = [seed + (i % 100) * 0.01 for i in range(DIM)]
    return struct.pack(f"{DIM}f", *vec)


def _write_note(vault, path: str, title: str, story: str = "") -> None:
    full_path = vault / path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(
        "---\n"
        f"title: {title}\n"
        f"story: {story}\n"
        "---\n\n"
        f"{title}\n\n{story}\n",
        encoding="utf-8",
    )


def _insert_capture(conn, capture_id: str, file_path: str, review_state: str = "new") -> None:
    conn.execute(
        "INSERT INTO captures (id, file_path, review_state, created_at) VALUES (?, ?, ?, ?)",
        (capture_id, file_path, review_state, "2026-06-01T00:00:00+00:00"),
    )


def test_refresh_builds_deterministic_knowledge_edges(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    vault = tmp_path / "vault"
    _write_note(vault, "captures/a.md", "Agent 工程", "工具和流程护栏")
    _write_note(vault, "captures/b.md", "Agent 评估", "工具和流程评估")
    _insert_capture(conn, "cap-a", "captures/a.md")
    _insert_capture(conn, "cap-b", "captures/b.md")

    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap-a",
        kind="identity",
        index_text="摘要：Agent 工程",
        embedding=_emb(1.0),
        source_hash="a",
        file_path="captures/a.md",
    )
    repo.upsert(
        capture_id="cap-b",
        kind="identity",
        index_text="摘要：Agent 评估",
        embedding=_emb(1.0),
        source_hash="b",
        file_path="captures/b.md",
    )
    repo.upsert(
        capture_id="cap-a",
        kind="section",
        section_id="s1",
        section_title="工具",
        index_text="工具护栏",
        embedding=_emb(2.0),
        source_hash="a-s1",
        file_path="captures/a.md",
    )
    repo.upsert(
        capture_id="cap-b",
        kind="section",
        section_id="s2",
        section_title="评估",
        index_text="工具评估",
        embedding=_emb(2.0),
        source_hash="b-s2",
        file_path="captures/b.md",
    )
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, one_liner, knowledge_links, capture_ids, status)
           VALUES (?, ?, ?, ?, ?, 'seed')""",
        ("card-a", "Agent 方法卡", "方法卡", json.dumps(["card-b"]), json.dumps(["cap-a"])),
    )
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, one_liner, knowledge_links, capture_ids, status)
           VALUES (?, ?, ?, ?, ?, 'seed')""",
        ("card-b", "Agent 风险卡", "风险卡", "[]", json.dumps(["cap-b"])),
    )
    conn.execute(
        """INSERT INTO pending_questions
           (id, agent_type, question_text, related_capture_ids, expires_at)
           VALUES (?, 'theme', ?, ?, ?)""",
        ("theme-1", "Agent 工程主题", json.dumps(["cap-a", "cap-b"]), "2099-01-01T00:00:00+00:00"),
    )

    service = KnowledgeGraphService(conn, vault)
    result = service.refresh()
    service.refresh()

    assert result["relation_counts"]["semantic_similarity"] == 1
    assert result["relation_counts"]["card_uses_capture"] == 2
    assert result["relation_counts"]["card_links_card"] == 1
    assert result["relation_counts"]["theme_groups_capture"] == 2
    assert conn.execute("SELECT COUNT(*) FROM knowledge_edges").fetchone()[0] == result["edge_count"]

    graph = service.focus_graph(focus_kind="capture", focus_id="cap-a")
    relation_types = {edge["relation_type"] for edge in graph["edges"]}
    assert {
        "semantic_similarity",
        "card_uses_capture",
        "theme_groups_capture",
    }.issubset(relation_types)
    capture_node = next(node for node in graph["nodes"] if node["id"] == "cap-a")
    assert capture_node["open_url"] == "/knowledge-library?kind=capture&item=cap-a"
    assert capture_node["file_url"].startswith("file://")


def test_focus_graph_defaults_to_latest_capture(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    vault = tmp_path / "vault"
    _write_note(vault, "captures/latest.md", "最新笔记")
    _insert_capture(conn, "latest", "captures/latest.md")

    graph = KnowledgeGraphService(conn, vault).focus_graph()

    assert graph["meta"]["focus_kind"] == "capture"
    assert graph["meta"]["focus_id"] == "latest"
    assert graph["nodes"][0]["id"] == "latest"


def test_refresh_only_uses_confirmed_readable_graph_materials(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    vault = tmp_path / "vault"
    _write_note(vault, "captures/live.md", "有效笔记")
    _write_note(vault, "captures/archived.md", "归档笔记")
    _insert_capture(conn, "cap-live", "captures/live.md")
    _insert_capture(conn, "cap-archived", "captures/archived.md", review_state="archived")
    _insert_capture(conn, "cap-missing", "captures/missing.md")

    repo = CaptureEmbeddingRepo(conn)
    for capture_id, file_path in (
        ("cap-live", "captures/live.md"),
        ("cap-archived", "captures/archived.md"),
        ("cap-missing", "captures/missing.md"),
    ):
        repo.upsert(
            capture_id=capture_id,
            kind="identity",
            index_text=f"摘要：{capture_id}",
            embedding=_emb(1.0),
            source_hash=capture_id,
            file_path=file_path,
        )

    conn.execute(
        """INSERT INTO capture_drafts
           (id, title, draft_text, status, committed_capture_id)
           VALUES ('draft-1', '草稿', '草稿内容', 'draft_ready', '')"""
    )
    conn.execute(
        """INSERT INTO garden_drafts
           (id, title, one_liner, capture_ids, status)
           VALUES ('garden-draft-1', '卡片草稿', '草稿摘要', ?, 'draft_ready')""",
        (json.dumps(["cap-live"]),),
    )
    conn.execute(
        """INSERT INTO reading_drafts
           (id, session_id, output_type, title, summary, draft_text, status)
           VALUES ('reading-draft-1', 'session-1', 'local_learning_note', '阅读草稿', '摘要', '正文', 'open')"""
    )
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, one_liner, knowledge_links, capture_ids, status)
           VALUES (?, ?, ?, ?, ?, 'seed')""",
        (
            "card-live",
            "有效卡片",
            "卡片摘要",
            json.dumps(["card-archived"]),
            json.dumps(["cap-live", "cap-archived", "cap-missing"]),
        ),
    )
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, one_liner, knowledge_links, capture_ids, status)
           VALUES (?, ?, ?, '[]', ?, 'archived')""",
        ("card-archived", "归档卡片", "归档摘要", json.dumps(["cap-live"])),
    )
    conn.execute(
        """INSERT INTO pending_questions
           (id, agent_type, question_text, related_capture_ids, expires_at)
           VALUES (?, 'theme', ?, ?, ?)""",
        (
            "theme-1",
            "主题候选",
            json.dumps(["cap-live", "cap-archived", "cap-missing"]),
            "2099-01-01T00:00:00+00:00",
        ),
    )

    service = KnowledgeGraphService(conn, vault)
    service.refresh()
    graph = service.focus_graph(focus_kind="garden_card", focus_id="card-live")
    capture_graph = service.focus_graph(focus_kind="capture", focus_id="cap-live")

    node_keys = {(node["kind"], node["id"]) for node in graph["nodes"]}
    edge_pairs = {(edge["relation_type"], edge["target_kind"], edge["target_id"]) for edge in graph["edges"]}
    capture_edge_pairs = {
        (edge["relation_type"], edge["target_kind"], edge["target_id"])
        for edge in capture_graph["edges"]
    }

    assert ("capture", "cap-live") in node_keys
    assert ("capture", "cap-archived") not in node_keys
    assert ("capture", "cap-missing") not in node_keys
    assert ("garden_card", "card-archived") not in node_keys
    assert ("card_uses_capture", "capture", "cap-live") in edge_pairs
    assert ("card_uses_capture", "capture", "cap-archived") not in edge_pairs
    assert ("theme_groups_capture", "capture", "cap-live") in capture_edge_pairs
    assert conn.execute("SELECT COUNT(*) FROM knowledge_edges WHERE source_kind LIKE '%draft%'").fetchone()[0] == 0
