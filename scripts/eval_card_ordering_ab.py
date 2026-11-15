"""Experiment 2 (structural half) — card-first vs evidence-first ordering.

The product question is whether surfacing a user's own card above the source
section helps or hurts.  The semantic half of that question ("does the answer
point out the boundary condition?") needs an LLM judge and an API key; this
script measures the part that does not: where the gold unit lands under the two
orderings, and whether card-first displaces correct evidence.

Usage::

    python scripts/eval_card_ordering_ab.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
for path in (PROJECT_ROOT, SCRIPTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from db.embedding_repo import CaptureEmbeddingRepo  # noqa: E402
from eval_rag_benchmark import (  # noqa: E402
    QUERIES,
    _blob,
    _card_hits,
    _dedupe,
    _keywords,
    _vector_hits,
    build_corpus,
)

CARD_QUERIES = [query for query in QUERIES if query["kind"] in {"card", "semantic"}]


def _evidence_first(conn, query: str, limit: int = 5) -> list[str]:
    evidence = _vector_hits(conn, query, limit)
    cards = _card_hits(conn, query, 3)
    return _dedupe(evidence + cards, limit)


def _card_first(conn, query: str, limit: int = 5) -> list[str]:
    cards = _card_hits(conn, query, 3)
    evidence = _vector_hits(conn, query, limit)
    return _dedupe(cards + evidence, limit)


def _rank(hits: list[str], gold: list[str]) -> int | None:
    for index, item in enumerate(hits, 1):
        if item in gold:
            return index
    return None


def run(work_dir: Path) -> dict:
    conn, _units, _cards = build_corpus(work_dir)
    try:
        report = {
            "queries": [],
            "evidence_first": {"hit": 0, "mrr": 0.0},
            "card_first": {"hit": 0, "mrr": 0.0},
        }
        for query in CARD_QUERIES:
            if not query["gold"]:
                continue
            a_hits = _evidence_first(conn, query["query"])
            b_hits = _card_first(conn, query["query"])
            a_rank = _rank(a_hits, query["gold"])
            b_rank = _rank(b_hits, query["gold"])
            for name, rank in (("evidence_first", a_rank), ("card_first", b_rank)):
                if rank:
                    report[name]["hit"] += 1
                    report[name]["mrr"] += 1.0 / rank
            report["queries"].append(
                {
                    "id": query["id"],
                    "evidence_first_rank": a_rank,
                    "card_first_rank": b_rank,
                    "delta": None
                    if a_rank is None or b_rank is None
                    else b_rank - a_rank,
                }
            )
        total = max(1, len([q for q in CARD_QUERIES if q["gold"]]))
        for name in ("evidence_first", "card_first"):
            report[name]["hit_rate"] = round(report[name]["hit"] / total, 3)
            report[name]["mrr"] = round(report[name]["mrr"] / total, 3)
        report["verdict"] = (
            "card-first 未损害排序"
            if report["card_first"]["mrr"] >= report["evidence_first"]["mrr"]
            else "card-first 把相关证据挤后了，需要按 source_count 降权"
        )
        report["llm_judge_note"] = (
            "语义半边（是否指出边界条件）需要模型判官；"
            "配置 LLM key 后对同一批 query 跑两份答案再比较。"
        )
        return report
    finally:
        conn.close()


def main() -> int:
    import tempfile

    work = Path(tempfile.mkdtemp(prefix="hermes-card-ab-"))
    report = run(work)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
