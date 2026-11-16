"""Contract tests for the evidence-first source slot allocator."""
from __future__ import annotations

from bridge.retrieval_ranking import rank_sources


def _evidence(item_id: str, score: float) -> dict:
    return {"id": item_id, "matched_kind": "section", "score": score}


def _keyword(item_id: str, score: float) -> dict:
    return {"id": item_id, "matched_kind": "section", "score": score}


def _card(item_id: str, score: float, source_count: int = 1) -> dict:
    return {
        "id": item_id,
        "matched_kind": "garden_card",
        "score": score,
        "source_count": source_count,
        "vec_score": score,
        "literal_score": 0.0,
        "citation_count": 0,
    }


def test_evidence_and_keyword_reserved_seats_are_present():
    ranked = rank_sources(
        evidence=[_evidence("e1", 0.9), _evidence("e2", 0.8), _evidence("e3", 0.7)],
        keywords=[_keyword("k1", 0.6), _keyword("k2", 0.5), _keyword("k3", 0.4)],
        cards=[_card("c1", 0.95), _card("c2", 0.85)],
    )
    assert len(ranked) == 7
    ids = [item["id"] for item in ranked]
    assert {"e1", "e2"} <= set(ids)
    assert {"k1", "k2"} <= set(ids)


def test_isolated_card_never_outranks_evidence():
    ranked = rank_sources(
        evidence=[_evidence("e1", 0.5)],
        keywords=[],
        cards=[_card("isolated", 1.0, source_count=0)],
    )
    assert [item["id"] for item in ranked] == ["e1", "isolated"]


def test_one_supported_card_may_lead_but_never_two():
    ranked = rank_sources(
        evidence=[_evidence("e1", 0.8)],
        keywords=[],
        cards=[_card("c1", 0.95), _card("c2", 0.9)],
    )
    ids = [item["id"] for item in ranked]
    assert ids[0] == "c1"
    assert ids.index("e1") < ids.index("c2")
