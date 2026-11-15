"""Smoke contract for the reproducible retrieval benchmark."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_benchmark():
    path = ROOT / "scripts" / "eval_rag_benchmark.py"
    spec = importlib.util.spec_from_file_location("eval_rag_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module  # dataclasses resolve their module at creation
    spec.loader.exec_module(module)
    return module


def test_corpus_is_large_enough_to_be_meaningful(tmp_path):
    module = _load_benchmark()
    report = module.run_benchmark(tmp_path, threshold=0.20)
    corpus = report["corpus"]
    assert corpus["notes"] == 20
    assert corpus["cards"] == 12
    assert corpus["units"] >= 90, "语料必须接近百个检索单元，否则 Recall 没有统计区分度"
    assert corpus["queries"] == 20
    assert corpus["positives"] == 18
    assert corpus["negatives"] == 2


def test_ablation_groups_are_reported(tmp_path):
    module = _load_benchmark()
    report = module.run_benchmark(tmp_path, threshold=0.20)
    groups = report["groups"]
    assert set(groups) == set(module.GROUPS)
    for row in groups.values():
        assert 0.0 <= row["recall_at_3"] <= 1.0
        assert 0.0 <= row["recall_at_5"] <= 1.0
        assert 0.0 <= row["mrr_at_5"] <= 1.0
        assert row["latency_p50_ms"] >= 0
        assert row["latency_p95_ms"] >= row["latency_p50_ms"]


def test_refactored_groups_beat_legacy_baseline(tmp_path):
    module = _load_benchmark()
    report = module.run_benchmark(tmp_path, threshold=0.20)
    legacy = report["groups"]["G0_title_summary_substring"]
    vector = report["groups"]["G1_vector_only"]
    full = report["groups"]["G3_full_v3"]
    assert vector["recall_at_5"] >= legacy["recall_at_5"]
    assert full["recall_at_5"] >= legacy["recall_at_5"]
    assert full["negative_rejection"] >= legacy["negative_rejection"]
    # 逐条明细必须落盘，否则"哪条 query 命中"无法复核。
    assert len(report["per_query_details"]) == 20
    assert all(entry["hits"] for entry in report["per_query_details"])
