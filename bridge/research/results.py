"""Result assembly and evidence-status helpers for web research."""
from __future__ import annotations

from typing import Any

from bridge.research.evidence import (
    _apply_critic_rejections_to_classified,
    _classify_ranked_sources,
    _web_provider_status,
)
from bridge.research.search import (
    _independent_source_count,
    _minimum_reliable_source_count,
    _missing_minimum_reliable_sources,
    _missing_preferred_domain_adoption,
    _missing_required_preferred_source,
)


def research_provider_status(
    started: float,
    warnings: list[str],
    warning_start: int,
    quality: dict[str, Any],
    fallback_attempts: list[dict[str, Any]],
    *,
    sources: list[dict[str, Any]] | None = None,
    stage_timings: dict[str, Any] | None = None,
    query_statuses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _web_provider_status(
        started,
        warnings,
        warning_start,
        sources=sources,
        retrieval_health=quality.get("retrieval_health") or {},
        fallback_attempts=fallback_attempts,
        stage_timings=stage_timings,
        query_statuses=query_statuses,
    )


def fallback_attempt(
    *,
    backend: str,
    queries: list[str],
    results: list[dict[str, Any]],
    new_result_count: int,
    trigger: str,
) -> dict[str, Any]:
    return {
        "backend": backend,
        "query_count": len(queries),
        "result_count": len(results),
        "new_result_count": new_result_count,
        "trigger": trigger,
    }


def attempted_fallback_backends(fallback_attempts: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("backend") or "") for item in fallback_attempts}


def mark_extraction_evidence_status(
    sources: list[dict[str, Any]],
    critic_rejected_sources: list[dict[str, Any]],
    quality: dict[str, Any],
) -> None:
    if critic_rejected_sources and not sources:
        quality["passed"] = False
        quality["reason"] = "evidence_critic_rejected_all"
        quality["evidence_status"] = "insufficient"
    elif not sources:
        quality["passed"] = False
        quality["reason"] = "no_extractable_sources"
        quality["evidence_status"] = "insufficient"


def mark_sufficient_evidence_status(
    sources: list[dict[str, Any]],
    research_plan: dict[str, Any],
    quality: dict[str, Any],
) -> None:
    if sources and not _missing_minimum_reliable_sources(research_plan, sources):
        quality["passed"] = True
        quality["reason"] = "passed"
        quality["evidence_status"] = "sufficient"


def mark_limited_evidence_status(
    sources: list[dict[str, Any]],
    research_plan: dict[str, Any],
    quality: dict[str, Any],
    warnings: list[str],
) -> None:
    if sources and _missing_minimum_reliable_sources(research_plan, sources):
        minimum = _minimum_reliable_source_count(research_plan.get("evidence_contract") or {})
        count = _independent_source_count(sources)
        warnings.append(f"可靠来源较少：该类问题理想情况下至少需要 {minimum} 个独立可靠来源，当前仅找到 {count} 个；已按有限证据回答。")
        quality["passed"] = True
        quality["reason"] = "limited_evidence"
        quality["evidence_status"] = "limited"
    if sources and _missing_preferred_domain_adoption(research_plan, sources):
        warnings.append("未取得 planner 首选官方/文档来源的可用证据，已基于通过证据裁判的其它可靠来源回答。")


def classified_web_result(
    sources: list[dict[str, Any]],
    *,
    query: str,
    ranked: list[dict[str, Any]],
    critic_rejected_sources: list[dict[str, Any]],
    queries: list[str],
    research_plan: dict[str, Any],
    search_quality: dict[str, Any],
    rewritten_queries: list[str],
    provider_status: dict[str, Any],
    adopted_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    classified = _classify_ranked_sources(
        query,
        ranked,
        source_preferences=research_plan.get("source_preferences") or {},
        subject=research_plan.get("subject") or {},
        evidence_contract=research_plan.get("evidence_contract") or {},
        adopted_sources=adopted_sources,
    )
    classified = _apply_critic_rejections_to_classified(classified, critic_rejected_sources)
    return web_research_result(
        sources,
        classified=classified,
        queries=queries,
        research_plan=research_plan,
        search_quality=search_quality,
        rewritten_queries=rewritten_queries,
        provider_status=provider_status,
    )


def missing_required_preferred_result(
    *,
    query: str,
    ranked: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    critic_rejected_sources: list[dict[str, Any]],
    queries: list[str],
    research_plan: dict[str, Any],
    quality: dict[str, Any],
    rewritten_queries: list[str],
    started: float,
    warnings: list[str],
    warning_start: int,
    fallback_attempts: list[dict[str, Any]],
    stage_timings: dict[str, Any] | None = None,
    query_statuses: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not _missing_required_preferred_source(research_plan, sources):
        return None
    warnings.append("未取得 planner 要求的官方/原始来源正文，已拒绝基于二手来源给出确定结论。")
    quality["passed"] = False
    quality["reason"] = "missing_required_preferred_source"
    provider_status = research_provider_status(
        started,
        warnings,
        warning_start,
        quality,
        fallback_attempts,
        sources=sources,
        stage_timings=stage_timings,
        query_statuses=query_statuses,
    )
    return classified_web_result(
        [],
        query=query,
        ranked=ranked,
        critic_rejected_sources=critic_rejected_sources,
        queries=queries,
        research_plan=research_plan,
        search_quality=quality,
        rewritten_queries=rewritten_queries,
        provider_status=provider_status,
        adopted_sources=[],
    )


def web_research_result(
    sources: list[dict[str, Any]],
    *,
    classified: dict[str, list[dict[str, Any]]],
    queries: list[str],
    research_plan: dict[str, Any],
    search_quality: dict[str, Any],
    rewritten_queries: list[str],
    provider_status: dict[str, Any],
) -> dict[str, Any]:
    return {
        "sources": sources,
        "retrieved_sources": classified.get("candidate_sources") or [],
        "clue_sources": classified.get("clue_sources") or [],
        "rejected_sources": classified.get("rejected_sources") or [],
        "queries": queries,
        "research_plan": research_plan,
        "search_quality": search_quality,
        "rewritten_queries": rewritten_queries,
        "provider_status": provider_status,
    }
