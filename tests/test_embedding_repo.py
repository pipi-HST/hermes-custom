from __future__ import annotations

import struct

from agents.embedder import DIM
from db.embedding_repo import CaptureEmbeddingRepo
from db.schema import init_db


def _emb(value: float) -> bytes:
    return struct.pack(f"{DIM}f", *([value] * DIM))


def test_upsert_identity_embedding_replaces_existing_row(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    repo = CaptureEmbeddingRepo(conn)

    repo.upsert(
        capture_id="cap1",
        kind="identity",
        section_id="",
        section_title="",
        index_text="标题：A\n摘要：旧",
        embedding=b"old",
        source_hash="h1",
        file_path="captures/a.md",
    )
    repo.upsert(
        capture_id="cap1",
        kind="identity",
        section_id="",
        section_title="",
        index_text="标题：A\n摘要：新",
        embedding=b"new",
        source_hash="h2",
        file_path="captures/a.md",
    )

    rows = repo.list_by_capture("cap1")
    assert len(rows) == 1
    assert rows[0]["index_text"].endswith("摘要：新")
    assert rows[0]["embedding"] == b"new"


def test_list_stale_rows_detects_hash_changes(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="identity",
        section_id="",
        section_title="",
        index_text="old",
        embedding=b"old",
        source_hash="old-hash",
        file_path="captures/a.md",
    )

    stale = repo.needs_reindex("cap1", "identity", "", "new-hash")
    fresh = repo.needs_reindex("cap1", "identity", "", "old-hash")

    assert stale is True
    assert fresh is False


def test_search_returns_rows_by_cosine_similarity(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="identity",
        section_id="",
        section_title="",
        index_text="positive",
        embedding=_emb(1.0),
        source_hash="h1",
        file_path="captures/a.md",
    )
    repo.upsert(
        capture_id="cap2",
        kind="section",
        section_id="s1",
        section_title="Section",
        index_text="negative",
        embedding=_emb(-1.0),
        source_hash="h2",
        file_path="captures/b.md",
    )

    rows = repo.search(_emb(1.0), kinds=("identity", "section"), limit=2)

    assert [row["capture_id"] for row in rows] == ["cap1", "cap2"]
    assert rows[0]["score"] > rows[1]["score"]


def test_search_scores_lightweight_candidates_before_loading_full_rows(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="identity",
        section_id="",
        section_title="",
        index_text="positive full row",
        embedding=_emb(1.0),
        source_hash="h1",
        file_path="captures/a.md",
    )
    repo.upsert(
        capture_id="cap2",
        kind="section",
        section_id="s1",
        section_title="Section",
        index_text="negative full row",
        embedding=_emb(-1.0),
        source_hash="h2",
        file_path="captures/b.md",
    )

    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    rows = repo.search(_emb(1.0), kinds=("identity", "section"), limit=1)
    conn.set_trace_callback(None)

    candidate_selects = [
        sql
        for sql in statements
        if "FROM capture_embeddings" in sql
        and "embedding IS NOT NULL" in sql
    ]
    assert candidate_selects
    normalized = " ".join(candidate_selects[0].split())
    assert "ce.id" in normalized and "ce.embedding" in normalized
    assert "ce.capture_id" in normalized and "ce.kind" in normalized
    assert "LEFT JOIN captures" in normalized
    assert "review_state" in normalized and "archived" in normalized
    assert "SELECT *" not in candidate_selects[0]
    assert [row["capture_id"] for row in rows] == ["cap1"]
    assert rows[0]["index_text"] == "positive full row"
