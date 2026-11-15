"""Reproduce the two blocking findings of ``docs/CODE-REVIEW-V3.md``.

P1: ``agents.rag_agent.build_rag_prompt`` dedupes captures by capture id, while
    ``bridge.rag_bridge._search_knowledge_base`` builds ``source_map`` from the
    *undeduped* list, so a prompt ``<source id=n>`` and a citation ``[n]``
    resolve to different documents.
P2: ``agents.rag_graph._select_final_hits`` returns several rows for the same
    capture id (one per section of an expanded capture), which is what feeds P1
    in production.

Run from the repo root::

    .venv/Scripts/python.exe scripts/repro_v3_review_findings.py
"""
import os
import re
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.rag_agent import _dedupe_captures, build_rag_prompt  # noqa: E402


def p1():
    print("=== P1: prompt source ids vs source_map refs ===")
    results = [
        {"id": "cap_a", "title": "A", "body": "A-body", "matched_kind": "section",
         "section_id": "section-1", "section_title": "S1"},
        {"id": "cap_b", "title": "B", "body": "B-body", "matched_kind": "identity",
         "section_id": "", "section_title": ""},
        {"id": "cap_a", "title": "A", "body": "A-body-2", "matched_kind": "section",
         "section_id": "section-3", "section_title": "S3"},
        {"id": "cap_c", "title": "C", "body": "C-body", "matched_kind": "identity",
         "section_id": "", "section_title": ""},
    ]
    # Exactly what bridge/rag_bridge.py:276 does.
    source_map = [
        {"ref": str(i), "id": item["id"], "section": item["section_title"]}
        for i, item in enumerate(results, 1)
    ]
    prompt = build_rag_prompt("问题", results)
    prompt_ids = re.findall(r'<source id="(\d+)"', prompt)
    print("  results          :", [r["id"] for r in results])
    print("  source_map refs  :", [(s["ref"], s["id"], s["section"]) for s in source_map])
    print("  prompt source ids:", prompt_ids)
    print("  deduped count    :", len(_dedupe_captures(results)))
    print("  MISMATCH         : %s sources in prompt vs %s refs in source_map"
          % (len(prompt_ids), len(source_map)))
    print("  model writes [3] meaning %r -> source_map ref 3 says %r"
          % (_dedupe_captures(results)[2]["id"], source_map[2]["id"]))


def p2():
    print()
    print("=== P2: _select_final_hits duplicate capture ids ===")
    import numpy as np

    from agents.rag_graph import _select_final_hits
    from db.embedding_repo import CaptureEmbeddingRepo
    from db.schema import init_db

    class FakeEmbedder:
        """Deterministic 1024-dim blobs; only ranking behaviour matters here."""

        DIM = 1024

        def embed(self, text: str) -> bytes:
            rng = np.random.default_rng(abs(hash(text)) % (2**32))
            return struct.pack(f"{self.DIM}f", *rng.normal(size=self.DIM).tolist())

    tmp = tempfile.mkdtemp()
    conn = init_db(os.path.join(tmp, "t.db"))
    emb = FakeEmbedder()
    repo = CaptureEmbeddingRepo(conn)
    query_vec = emb.embed("Agent 工具调用收敛策略 与上下文预算")
    repo.upsert(capture_id="cap_a", kind="identity", section_id="", section_title="",
                index_text="整篇", source_hash="h0", file_path="notes/a.md",
                embedding=emb.embed("整篇"))
    for i in (1, 2, 3):
        repo.upsert(capture_id="cap_a", kind="section", section_id=f"section-{i}",
                    section_title=f"第{i}节", index_text=f"第{i}节 内容",
                    source_hash=f"h{i}", file_path="notes/a.md",
                    embedding=emb.embed(f"第{i}节 Agent 工具调用"))
    print("  embedding rows  :",
          conn.execute("select count(*) from capture_embeddings").fetchone()[0])
    hits = _select_final_hits(conn, query_embedding=query_vec, seed_hits=[],
                              expanded_capture_ids=["cap_a"], final_limit=6)
    for hit in hits:
        print("    id=%-6s matched_kind=%-8s section_id=%-10s score=%.3f" % (
            hit.get("id"), hit.get("matched_kind"),
            hit.get("section_id") or "-", hit.get("score", 0)))
    ids = [hit.get("id") for hit in hits]
    print("  final_hits      : %s rows, %s distinct capture ids"
          % (len(ids), len(set(ids))))


if __name__ == "__main__":
    p1()
    p2()
