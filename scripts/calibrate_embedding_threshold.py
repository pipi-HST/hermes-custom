"""Calibrate the vector relevance gate from measured score distributions.

`MIN_VECTOR_SCORE = 0.20` was a guess.  This script measures the actual
positive/negative cosine distributions for the *engine in use* and proposes a
threshold at the midpoint between Q95(negative) and Q10(positive).

The engine matters: a semantic embedding model and an offline char-ngram hash
produce completely different distributions, so a threshold calibrated on one
must not be reused for the other.  The report states which engine ran.

Usage::

    python scripts/calibrate_embedding_threshold.py
    python scripts/calibrate_embedding_threshold.py --json-out out/calib.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
for path in (PROJECT_ROOT, SCRIPTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from eval_rag_benchmark import (  # noqa: E402
    DISTRACTOR_TOPICS,
    DOCS,
    ENGINE_OFFLINE,
    _hash_vector,
)

# Deliberately different phrasing from the evaluation queries: calibrating on
# the eval set would leak the answers into the threshold.
POSITIVE_PAIRS: list[tuple[str, str]] = [
    ("需求真假先看替代方案", "cap_aipm_demand"),
    ("面试里判断伪需求的反例", "cap_aipm_demand"),
    ("多级转发为什么会丢失原文", "cap_telephone_game"),
    ("路由 Agent 压缩工具参数", "cap_telephone_game"),
    ("把串行抓取改并发", "cap_latency_roi"),
    ("重型检索管线延迟太高", "cap_latency_roi"),
    ("资料和卡片应该分几层", "cap_pkm_compare"),
    ("工具协议统一接入", "cap_mcp"),
    ("工具数量影响路由", "cap_mcp"),
    ("小模型先跑再升级", "cap_model_routing"),
    ("什么样的需求证据才算数", "cap_aipm_demand"),
    ("伪需求常见的四种说法", "cap_aipm_demand"),
    ("主 Agent 为什么改写了长文", "cap_telephone_game"),
    ("四道调用叠加的损耗", "cap_telephone_game"),
    ("七阶段里最不值得的阶段", "cap_latency_roi"),
    ("并发抓取把延迟变成最大值", "cap_latency_roi"),
    ("竞品的证据层边界", "cap_pkm_compare"),
    ("卡片与专题分别放什么", "cap_pkm_compare"),
    ("MCP 的 M×N 到 M+N", "cap_mcp"),
    ("工具超过二十个会发生什么", "cap_mcp"),
    ("抽取任务该用大模型吗", "cap_model_routing"),
    ("按 token 数做路由的误区", "cap_model_routing"),
    ("延迟预算怎么拆", "cap_latency_roi"),
    ("收藏之后怎么内化", "cap_pkm_compare"),
    ("工具描述重叠的代价", "cap_mcp"),
    ("小模型置信度低时降级", "cap_model_routing"),
    ("长文搬运不能经过模型参数", "cap_telephone_game"),
    ("替代方案很差才算真需求", "cap_aipm_demand"),
    ("引用校验为什么不降质量", "cap_latency_roi"),
    ("为什么不能让模型当搬运工", "cap_telephone_game"),
]

NEGATIVE_QUERIES = [
    "提拉米苏要不要放咖啡酒",
    "越位判罚的例外情况",
    "阳台番茄多久浇一次水",
    "基金定投什么时候止盈",
    "手冲咖啡水温多少合适",
    "露营需要带哪些装备",
    "英语听力怎么精听",
    "手机摄影怎么构图",
    "跑步膝盖疼怎么办",
    "红酒有哪些产区",
    "猫砂盆多久清理一次",
    "签证需要哪些材料",
]


def _doc_text(doc: dict) -> str:
    return "\n".join(f"{title}。{text}" for title, text in doc["sections"])


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator == 0:
        return 0.0
    return float(np.dot(left, right) / denominator)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(len(ordered) * fraction)) - 1))
    return round(ordered[index], 4)


def detect_engine() -> tuple[str, object]:
    key = os.environ.get("QWEN_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        try:
            from agents.capture_agent import load_config

            key = str(getattr(load_config(), "qwen_api_key", "") or "")
        except Exception:
            key = ""
    if key:
        try:
            from agents.embedder import Embedder

            embedder = Embedder(api_key=key)

            def _embed(text: str):
                blob = embedder.embed(text)
                return np.frombuffer(blob, dtype=np.float32)

            return "dashscope-v4", _embed
        except Exception:
            pass
    return ENGINE_OFFLINE, _hash_vector


def calibrate() -> dict:
    engine, embed = detect_engine()
    docs = {doc["id"]: doc for doc in DOCS}
    distractors = [
        _doc_text({"sections": [("背景", summary)]}) for _, summary in DISTRACTOR_TOPICS
    ]
    distractor_vectors = [embed(text) for text in distractors]

    positives: list[float] = []
    for query, doc_id in POSITIVE_PAIRS:
        query_vector = embed(query)
        positives.append(_cosine(query_vector, embed(_doc_text(docs[doc_id]))))

    negatives: list[float] = []
    for query in NEGATIVE_QUERIES:
        query_vector = embed(query)
        for vector in distractor_vectors:
            negatives.append(_cosine(query_vector, vector))

    q95_neg = _percentile(negatives, 0.95)
    q10_pos = _percentile(positives, 0.10)
    suggested = round((q95_neg + q10_pos) / 2, 2)
    return {
        "engine": engine,
        "positive_count": len(positives),
        "negative_count": len(negatives),
        "positive_p10": q10_pos,
        "positive_p50": round(statistics.median(positives), 4),
        "positive_p90": _percentile(positives, 0.90),
        "negative_p50": round(statistics.median(negatives), 4),
        "negative_p95": q95_neg,
        "separation": round(q10_pos - q95_neg, 4),
        "suggested_min_vector_score": suggested,
        "current_min_vector_score": 0.20,
        "note": (
            "hash 引擎衡量字面重合，阈值只在同一引擎内有效；"
            "配好 embedding key 后必须重跑本脚本"
            if engine == ENGINE_OFFLINE
            else "语义引擎实测分布，可直接用于生产阈值"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", default="")
    args = parser.parse_args(argv)
    report = calibrate()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
