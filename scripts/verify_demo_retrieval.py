"""Gate 3a: does the demo library answer six natural questions at 0.46?

Runs the *real* DashScope query embedding and the *real* two-stage vector
search against ``data/captures.db``, then reports the top-1 cosine per target
capture.  This is the check that decides whether ``MIN_VECTOR_SCORE`` can stay
at the calibrated 0.46 or has to be overridden in ``config.yaml``.

Usage::

    .venv/Scripts/python.exe scripts/verify_demo_retrieval.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.capture_agent import load_config  # noqa: E402
from agents.embedder import Embedder  # noqa: E402
from bridge.rag_bridge import MIN_VECTOR_SCORE  # noqa: E402
from db.embedding_repo import CaptureEmbeddingRepo  # noqa: E402
from db.schema import init_db  # noqa: E402

CASES = [
    ("cap_pm_interview_01", "怎么判断一个 AI 需求是真需求还是为了 AI 而 AI 的伪需求？"),
    ("cap_agent_arch_01", "多级 Agent 里模型互相转述为什么会丢信息？"),
    ("cap_agent_arch_02", "Research 管线从 130 秒降到 20 秒砍掉了哪些阶段？"),
    ("cap_pkm_teardown_01", "Notion AI、NotebookLM 和 IMA 这些个人知识库产品有什么区别？"),
    ("cap_agent_mcp_01", "MCP 能解决工具太多模型选错的问题吗？"),
    ("cap_model_routing_01", "大小模型怎么协同才能同时省成本和延迟？"),
]


def evaluate_rows(
    rows: list[dict], target_id: str, gate: float
) -> tuple[float, int, bool]:
    """Return (top1_score, target_rank, passed) for one demo case."""
    seen: list[str] = []
    best: dict[str, float] = {}
    for row in rows:
        cid = str(row.get("capture_id") or "")
        score = float(row.get("score") or 0.0)
        if cid and cid not in best:
            best[cid] = score
            seen.append(cid)
    ranked = [cid for cid in seen if best[cid] >= gate]
    top1 = max(best.values()) if best else 0.0
    rank = ranked.index(target_id) + 1 if target_id in ranked else -1
    return top1, rank, rank > 0


def main() -> int:
    config = load_config()
    conn = init_db(str(config.db_path))
    embedder = Embedder(api_key=config.qwen_api_key)
    repo = CaptureEmbeddingRepo(conn)
    gate = float(MIN_VECTOR_SCORE)
    print(f"MIN_VECTOR_SCORE = {gate}")
    print(f"{'query':<40} {'top1':>6} {'target':>7} {'rank':>5}  verdict")
    passed = 0
    for capture_id, query in CASES:
        rows = repo.search(embedder.embed(query), kinds=("identity", "section"), limit=20)
        top1, rank, ok = evaluate_rows(rows, capture_id, gate)
        passed += 1 if ok else 0
        print(
            f"{query[:38]:<40} {top1:>6.3f} {capture_id[:7]:>7} {rank:>5}  "
            f"{'HIT' if ok else 'MISS'}"
        )
    print(f"\n{passed}/{len(CASES)} hit at threshold {gate}")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
