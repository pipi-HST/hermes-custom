"""Unit coverage for the demo retrieval gate's ranking logic."""
from __future__ import annotations

from scripts.verify_demo_retrieval import evaluate_rows


def test_target_at_gate_passes_and_counts_rank():
    rows = [
        {"capture_id": "other", "score": 0.70},
        {"capture_id": "target", "score": 0.50},
    ]

    top1, rank, ok = evaluate_rows(rows, "target", 0.46)

    assert top1 == 0.70
    assert rank == 2
    assert ok is True


def test_target_below_gate_fails():
    rows = [
        {"capture_id": "target", "score": 0.30},
        {"capture_id": "other", "score": 0.20},
    ]

    top1, rank, ok = evaluate_rows(rows, "target", 0.46)

    assert top1 == 0.30
    assert rank == -1
    assert ok is False


def test_empty_rows_fail_without_crashing():
    assert evaluate_rows([], "target", 0.46) == (0.0, -1, False)
