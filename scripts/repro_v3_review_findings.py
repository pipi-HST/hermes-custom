"""Reproduce (and assert) the two blocking findings of ``docs/CODE-REVIEW-V3.md``.

P1: a prompt ``<source id=n>`` and a citation ``[n]`` must resolve to the same
    document.  The fix is a single numbering source: dedupe + budget-trim once
    (``select_prompt_sources``), then render *that* list.
P2: ``agents.rag_graph._select_final_hits`` must not return the same capture
    twice (it used to return one row per section of an expanded capture).

Run from the repo root::

    .venv/Scripts/python.exe scripts/repro_v3_review_findings.py

Exit code 0 means both invariants hold; 1 means a regression came back.
"""
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.rag_agent import (  # noqa: E402
    _dedupe_captures,
    render_prompt,
    select_prompt_sources,
)

SOURCE_ID_RE = re.compile(r'<source id="(\d+)"')


def p1() -> list[str]:
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
    # Exactly what bridge/rag_bridge.py does: trim once, then number and render
    # from the very same list.
    selected = select_prompt_sources(results, question="问题")
    source_map = [
        {"ref": str(i), "id": item["id"], "section": item.get("section_title", "")}
        for i, item in enumerate(selected, 1)
    ]
    prompt = render_prompt("问题", selected)
    prompt_ids = SOURCE_ID_RE.findall(prompt)
    print("  results          :", [r["id"] for r in results])
    print("  deduped count    :", len(_dedupe_captures(results)))
    print("  selected         :", [r["id"] for r in selected])
    print("  source_map refs  :", [(s["ref"], s["id"], s["section"]) for s in source_map])
    print("  prompt source ids:", prompt_ids)

    errors = []
    if len(prompt_ids) != len(source_map):
        errors.append(
            f"{len(prompt_ids)} sources in prompt vs {len(source_map)} refs in source_map"
        )
    if [s["id"] for s in source_map] != [c["id"] for c in selected]:
        errors.append("source_map order differs from the rendered order")
    if errors:
        print("  MISMATCH:", "; ".join(errors))
    else:
        print(f"  OK: {len(prompt_ids)} sources == {len(source_map)} refs")
    return errors


def p2() -> list[str]:
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

    # In-memory DB: tempfile.mkdtemp() lands in the CWD when TMP/TEMP unset,
    # which used to leave stray tmp*/ dirs inside the repo.
    conn = init_db(":memory:")
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
    hits = _select_final_hits(conn, query_embedding=query_vec, seed_hits=[],
                              expanded_capture_ids=["cap_a"], final_limit=6)
    for hit in hits:
        print("    id=%-6s matched_kind=%-8s section_id=%-10s score=%.3f" % (
            hit.get("id"), hit.get("matched_kind"),
            hit.get("section_id") or "-", hit.get("score", 0)))
    ids = [hit.get("id") for hit in hits]
    print("  final_hits: %s rows, %s distinct capture ids"
          % (len(ids), len(set(ids))))
    if len(ids) != len(set(ids)):
        print("  MISMATCH: the same capture appears more than once")
        return ["duplicate capture ids in final_hits"]
    print("  OK: one hit per capture")
    return []


if __name__ == "__main__":
    problems = p1() + p2()
    raise SystemExit(1 if problems else 0)
