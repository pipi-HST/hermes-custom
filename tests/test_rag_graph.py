from __future__ import annotations

import json
import struct

from agents.rag_graph import build_graph_retrieval
from db.embedding_repo import CaptureEmbeddingRepo
from db.schema import init_db


def _vec(first: float, second: float = 0.0) -> bytes:
    values = [0.0] * 1024
    values[0] = first
    values[1] = second
    return struct.pack("1024f", *values)


def test_graph_retrieval_expands_capture_seed_to_garden_captures(tmp_path):
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
            "AI Agent 工程化",
            '{"schema_version":"garden_card.v2","claim":"工程化来自工具、流程和护栏。","card_kind":"viewpoint","source_ids":["cap1","cap2"]}',
            json.dumps(["cap1", "cap2"], ensure_ascii=False),
            "2026-05-31T00:00:00+00:00",
            "2026-05-31T00:00:00+00:00",
        ),
    )
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="s1",
        section_title="工程化入口",
        index_text="AI Agent 工程化 工具 护栏",
        embedding=_vec(1.0, 0.0),
        source_hash="cap1-s1",
        file_path="captures/cap1.md",
    )
    repo.upsert(
        capture_id="cap2",
        kind="section",
        section_id="s2",
        section_title="工作流模式",
        index_text="workflow router evaluator optimizer",
        embedding=_vec(0.95, 0.0),
        source_hash="cap2-s2",
        file_path="captures/cap2.md",
    )

    result = build_graph_retrieval(
        conn,
        query_embedding=_vec(1.0, 0.0),
        seed_hits=[
            {
                "id": "cap1",
                "file_path": "captures/cap1.md",
                "matched_kind": "section",
                "section_id": "s1",
                "section_title": "工程化入口",
                "score": 1.0,
            }
        ],
        vault_path=str(tmp_path),
        final_limit=4,
    )

    assert result["expanded_garden_cards"][0]["title"] == "AI Agent 工程化"
    assert "cap2" in result["expanded_capture_ids"]
    assert [hit["id"] for hit in result["final_hits"]] == ["cap1", "cap2"]
    assert result["trace"]["garden_count"] == 1


def test_graph_retrieval_expansion_skips_archived_captures(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    for capture_id in ("cap1", "cap2", "cap3"):
        conn.execute(
            "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, ?)",
            (capture_id, f"captures/{capture_id}.md", "2026-05-31T00:00:00+00:00"),
        )
    conn.execute(
        "UPDATE captures SET archived_at = ? WHERE id = ?",
        ("2026-06-01T00:00:00+00:00", "cap2"),
    )
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, payload_json, status, capture_ids, created_at, updated_at, review_interval)
           VALUES (?, ?, ?, 'seed', ?, ?, ?, '1d')""",
        (
            "g1",
            "AI Agent 工程化",
            '{"schema_version":"garden_card.v2","claim":"工程化来自工具、流程和护栏。","card_kind":"viewpoint","source_ids":["cap1","cap2","cap3"]}',
            json.dumps(["cap1", "cap2", "cap3"], ensure_ascii=False),
            "2026-05-31T00:00:00+00:00",
            "2026-05-31T00:00:00+00:00",
        ),
    )
    repo = CaptureEmbeddingRepo(conn)
    for capture_id, score in (("cap1", 1.0), ("cap2", 0.95), ("cap3", 0.9)):
        repo.upsert(
            capture_id=capture_id,
            kind="section",
            section_id=f"{capture_id}-s",
            section_title="工程化",
            index_text=f"{capture_id} AI Agent 工程化",
            embedding=_vec(score, 0.0),
            source_hash=f"{capture_id}-s",
            file_path=f"captures/{capture_id}.md",
        )

    result = build_graph_retrieval(
        conn,
        query_embedding=_vec(1.0, 0.0),
        seed_hits=[
            {
                "id": "cap1",
                "file_path": "captures/cap1.md",
                "matched_kind": "section",
                "section_id": "cap1-s",
                "section_title": "工程化",
                "score": 1.0,
            }
        ],
        vault_path=str(tmp_path),
        final_limit=4,
        max_expanded_captures=2,
    )

    # cap2 is archived but sits before cap3 in the card: it must not spend the
    # second expansion slot.
    assert result["expanded_capture_ids"] == ["cap1", "cap3"]
    assert [hit["id"] for hit in result["final_hits"]] == ["cap1", "cap3"]


def test_graph_retrieval_skips_managed_sections_from_expanded_captures(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, ?)",
        ("cap1", "captures/cap1.md", "2026-05-31T00:00:00+00:00"),
    )
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="related",
        section_title="相关笔记",
        index_text="AI Agent 工程化",
        embedding=_vec(1.0, 0.0),
        source_hash="related",
        file_path="captures/cap1.md",
    )
    repo.upsert(
        capture_id="cap1",
        kind="identity",
        index_text="AI Agent 工程化 identity",
        embedding=_vec(0.8, 0.0),
        source_hash="identity",
        file_path="captures/cap1.md",
    )

    result = build_graph_retrieval(
        conn,
        query_embedding=_vec(1.0, 0.0),
        seed_hits=[{"id": "cap1", "file_path": "captures/cap1.md", "score": 1.0}],
        vault_path=str(tmp_path),
        final_limit=3,
    )

    assert result["final_hits"][0]["matched_kind"] == "identity"
    assert result["final_hits"][0]["section_title"] == ""


def test_graph_retrieval_prefers_sections_over_whole_note_for_same_capture(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, ?)",
        ("cap1", "captures/cap1.md", "2026-05-31T00:00:00+00:00"),
    )
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="s1",
        section_title="关键章节",
        index_text="AI Agent 工程化 Harness",
        embedding=_vec(0.95, 0.0),
        source_hash="cap1-s1",
        file_path="captures/cap1.md",
    )

    result = build_graph_retrieval(
        conn,
        query_embedding=_vec(1.0, 0.0),
        seed_hits=[
            {
                "id": "cap1",
                "file_path": "captures/cap1.md",
                "matched_kind": "identity",
                "section_id": "",
                "section_title": "",
                "score": 1.0,
            }
        ],
        vault_path=str(tmp_path),
        final_limit=3,
    )

    assert len(result["final_hits"]) == 1
    assert result["final_hits"][0]["matched_kind"] == "section"
    assert result["final_hits"][0]["section_title"] == "关键章节"


def test_graph_retrieval_ignores_seed_hits_without_file_path(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    result = build_graph_retrieval(
        conn,
        query_embedding=_vec(1.0, 0.0),
        seed_hits=[{"id": "cap1", "file_path": "", "matched_kind": "identity"}],
        vault_path=str(tmp_path),
        final_limit=3,
    )

    assert result["expanded_theme_pages"] == []
