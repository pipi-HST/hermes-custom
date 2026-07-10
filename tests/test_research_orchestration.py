from __future__ import annotations

import asyncio

from bridge.research import orchestration


def test_async_low_quality_result_returns_empty_research_result(monkeypatch):
    async def fake_collect(*_args, **_kwargs):
        return [{"url": "https://example.com/noisy", "title": "Noisy result"}]

    quality = {
        "passed": False,
        "reason": "low_quality_results",
        "retrieval_health": {},
    }
    classified = {
        "candidate_sources": [],
        "clue_sources": [],
        "rejected_sources": [],
    }
    monkeypatch.setattr(orchestration, "collect_web_search_results_async", fake_collect)
    monkeypatch.setattr(
        orchestration,
        "_rank_and_evaluate_results",
        lambda *_args: ([{"url": "https://example.com/noisy"}], dict(quality)),
    )
    monkeypatch.setattr(
        orchestration,
        "_classify_ranked_sources",
        lambda *_args, **_kwargs: classified,
    )
    monkeypatch.setattr(
        orchestration,
        "_should_let_critic_judge_quality_failure",
        lambda *_args: False,
    )
    monkeypatch.setattr(orchestration, "_web_sources_from_ranked", lambda *_args: [])

    deps = orchestration.WebResearchDeps(
        default_web_search=lambda *_args, **_kwargs: {},
        default_web_extract=lambda *_args, **_kwargs: {},
        default_search_backend=lambda: "test",
        available_fallback_search_backends=lambda _backend: [],
        fallback_search_adapter=lambda _backend: lambda *_args, **_kwargs: {},
        research_budget_available=lambda _seconds: False,
    )
    plan = {
        "queries": ["test query"],
        "source_preferences": {},
        "subject": {},
        "evidence_contract": {},
    }

    result = asyncio.run(
        orchestration.run_web_research_async(
            "test query",
            [],
            research_plan=plan,
            search_adapter=lambda *_args, **_kwargs: {},
            extract_adapter=lambda *_args, **_kwargs: {},
            deps=deps,
        )
    )

    assert result["sources"] == []
    assert result["search_quality"]["reason"] == "low_quality_results"
