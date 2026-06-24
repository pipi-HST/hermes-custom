from __future__ import annotations

import struct

from agents.embedder import DIM
from db.schema import init_db
from db.vector_search import search


def _emb(first: float) -> bytes:
    values = [0.0] * DIM
    values[0] = first
    return struct.pack(f"{DIM}f", *values)


class _FakeEmbedder:
    def embed(self, _query: str) -> bytes:
        return _emb(1.0)


class _RepoWithoutListAll:
    def __init__(self, conn):
        self.conn = conn

    def list_all(self, **_kwargs):
        raise AssertionError("vector_search should read embedding candidates directly")


def test_vector_search_reads_only_embedding_candidates_from_db(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    conn.execute(
        "INSERT INTO captures (id, file_path, embedding, created_at) VALUES (?, ?, ?, ?)",
        ("positive", "captures/positive.md", _emb(1.0), "2026-06-03T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO captures (id, file_path, embedding, created_at) VALUES (?, ?, ?, ?)",
        ("negative", "captures/negative.md", _emb(-1.0), "2026-06-02T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO captures (id, file_path, embedding, created_at) VALUES (?, ?, ?, ?)",
        ("missing", "captures/missing.md", None, "2026-06-01T00:00:00+00:00"),
    )
    conn.commit()

    results = search(
        "query",
        _RepoWithoutListAll(conn),
        _FakeEmbedder(),
        top_k=1,
    )

    assert [item["id"] for item in results] == ["positive"]
