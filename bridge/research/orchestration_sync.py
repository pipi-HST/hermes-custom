"""Synchronous web research orchestration for Hermes research."""
from __future__ import annotations

import time
from typing import Any

from bridge.research.types import (
    MAX_WEB_CANDIDATES,
    ResearchExtractAdapter,
    ResearchSearchAdapter,
)
from bridge.research.utils import (
    _dedupe_web_results,
    _sanitize_evidence_contract,
)
from bridge.research.planner import (
    _limited_search_queries,
    _max_initial_search_queries_for_plan,
    _max_repair_search_queries_for_plan,
    plan_research,
)
from bridge.research.search import (
    _independent_source_count,
    _max_extract_urls_for_plan,
    _missing_minimum_reliable_sources,
    _results_include_preferred_domain,
    _should_let_critic_judge_quality_failure,
)
from bridge.research.extraction import extract_and_criticize_sources
from bridge.research.results import (
    attempted_fallback_backends,
    classified_web_result,
    fallback_attempt,
    mark_extraction_evidence_status,
    mark_limited_evidence_status,
    mark_sufficient_evidence_status,
    missing_required_preferred_result,
    research_provider_status,
)
from bridge.research.search_infra import (
    _direct_mentioned_url_candidates,
    _direct_retrieval_candidates,
    _friendly_search_error,
    _post_critic_repair_queries,
    _preferred_domain_limited_queries,
    _queries_with_preferred_domains,
    _rank_and_evaluate_results,
    collect_web_search_results,
    rewrite_queries_for_low_quality_results,
)


def run_web_research(
    query: str,
    warnings: list[str],
    *,
    research_plan: dict[str, Any] | None = None,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    search_adapter: ResearchSearchAdapter | None,
    extract_adapter: ResearchExtractAdapter | None,
    deps: WebResearchDeps,
) -> dict[str, Any]:
    using_default_search = search_adapter is None
    search_adapter = search_adapter or deps.default_web_search
    extract_adapter = extract_adapter or deps.default_web_extract
    research_plan = research_plan or plan_research(
        query,
        mode="local_web",
        article_context=article_context,
        selection_context=selection_context,
    )
    initial_query_limit = _max_initial_search_queries_for_plan(research_plan)
    repair_query_limit = _max_repair_search_queries_for_plan(research_plan)
    queries = _queries_with_preferred_domains(
        list(research_plan.get("queries") or []),
        research_plan.get("source_preferences") or {},
        max_queries=initial_query_limit,
    )
    warning_start = len(warnings)
    started = time.monotonic()
    fallback_attempts: list[dict[str, Any]] = []
    primary_backend = deps.default_search_backend() if using_default_search else ""
    direct_candidates = (
        _direct_retrieval_candidates(query, research_plan, existing_results=[])
        if using_default_search
        else []
    )
    results = collect_web_search_results(
        queries,
        search_adapter=search_adapter,
        warnings=warnings,
        backend_label=primary_backend,
        max_queries=initial_query_limit,
    )
    if using_default_search:
        results = _dedupe_web_results(direct_candidates + results)[:MAX_WEB_CANDIDATES]
        mentioned_url_candidates = _direct_mentioned_url_candidates(results, research_plan)
        if mentioned_url_candidates:
            results = _dedupe_web_results(mentioned_url_candidates + results)[:MAX_WEB_CANDIDATES]
        preferred_domain_queries = _preferred_domain_limited_queries(queries, research_plan.get("source_preferences") or {})
        if preferred_domain_queries and not _results_include_preferred_domain(results, research_plan.get("source_preferences") or {}) and deps.research_budget_available(8.0):
            attempted_backends = {primary_backend}
            for backend in deps.available_fallback_search_backends(primary_backend):
                if backend in attempted_backends:
                    continue
                before_count = len(results)
                try:
                    preferred_results = collect_web_search_results(
                        preferred_domain_queries,
                        search_adapter=deps.fallback_search_adapter(backend),
                        warnings=warnings,
                        backend_label=backend,
                        max_queries=repair_query_limit,
                    )
                    preferred_results = [{**item, "fallback_candidate": True} for item in preferred_results]
                except Exception as exc:
                    preferred_results = []
                    warnings.append(f"优先域名补充搜索失败（{backend}）：{_friendly_search_error(exc)}")
                results = _dedupe_web_results(preferred_results + results)[:MAX_WEB_CANDIDATES]
                fallback_attempts.append(
                    fallback_attempt(
                        backend=backend,
                        queries=preferred_domain_queries,
                        results=preferred_results,
                        new_result_count=max(len(results) - before_count, 0),
                        trigger="preferred_domain_missing",
                    )
                )
                if _results_include_preferred_domain(results, research_plan.get("source_preferences") or {}):
                    break
                attempted_backends.add(backend)
    ranked, quality = _rank_and_evaluate_results(query, results, research_plan)
    rewritten_queries: list[str] = []
    if results and not quality["passed"] and deps.research_budget_available(5.0):
        rewritten_queries = rewrite_queries_for_low_quality_results(
            query,
            existing_queries=queries,
            failed_results=ranked,
            article_context=article_context,
            selection_context=selection_context,
        )
        if rewritten_queries:
            rewritten_results = collect_web_search_results(
                rewritten_queries,
                search_adapter=search_adapter,
                warnings=warnings,
                backend_label=primary_backend,
                max_queries=repair_query_limit,
            )
            results = _dedupe_web_results(rewritten_results + results)
            ranked, quality = _rank_and_evaluate_results(query, results, research_plan)
            quality["rewrite_attempted"] = True
            quality["rewritten_queries"] = rewritten_queries
    if using_default_search and (quality.get("retrieval_health") or {}).get("needs_recall_repair") and deps.research_budget_available(8.0):
        fallback_queries = _limited_search_queries(rewritten_queries or queries, repair_query_limit)
        for backend in deps.available_fallback_search_backends(primary_backend):
            before_count = len(results)
            try:
                fallback_results = collect_web_search_results(
                    fallback_queries,
                    search_adapter=deps.fallback_search_adapter(backend),
                    warnings=warnings,
                    backend_label=backend,
                    max_queries=repair_query_limit,
                )
                fallback_results = [{**item, "fallback_candidate": True} for item in fallback_results]
            except Exception as exc:
                fallback_results = []
                warnings.append(f"补充搜索失败（{backend}）：{_friendly_search_error(exc)}")
            results = _dedupe_web_results(fallback_results + results)[:MAX_WEB_CANDIDATES]
            ranked, quality = _rank_and_evaluate_results(query, results, research_plan)
            fallback_attempts.append(
                fallback_attempt(
                    backend=backend,
                    queries=fallback_queries,
                    results=fallback_results,
                    new_result_count=max(len(results) - before_count, 0),
                    trigger=(quality.get("retrieval_health") or {}).get("repair_reason") or "",
                )
            )
            if quality.get("passed") and not (quality.get("retrieval_health") or {}).get("needs_recall_repair"):
                break
    provider_status = research_provider_status(
        started,
        warnings,
        warning_start,
        quality,
        fallback_attempts,
    )
    if results and not quality["passed"] and not _should_let_critic_judge_quality_failure(quality, research_plan):
        if (
            quality.get("reason") == "no preferred or trusted source matched planner source preferences"
            and _sanitize_evidence_contract(research_plan.get("evidence_contract") or {}).get("must_use_primary_source")
        ):
            quality["reason"] = "missing_required_preferred_source"
            warnings.append("未取得 planner 要求的官方/原始来源正文，已拒绝基于二手来源给出确定结论。")
        warnings.append("联网搜索结果相关性不足，已尝试重写搜索词；仍未找到足够可靠来源。")
        provider_status = research_provider_status(
            started,
            warnings,
            warning_start,
            quality,
            fallback_attempts,
        )
        return classified_web_result(
            [],
            query=query,
            ranked=ranked,
            critic_rejected_sources=[],
            queries=queries,
            research_plan=research_plan,
            search_quality=quality,
            rewritten_queries=rewritten_queries,
            provider_status=provider_status,
        )
    extract_limit = _max_extract_urls_for_plan(research_plan)
    sources, critic_rejected_sources = extract_and_criticize_sources(
        query,
        ranked,
        research_plan,
        extract_adapter,
        warnings,
        extract_limit=extract_limit,
    )
    if using_default_search and critic_rejected_sources and not sources and deps.research_budget_available(8.0):
        attempted_backends = attempted_fallback_backends(fallback_attempts)
        for backend in deps.available_fallback_search_backends(primary_backend):
            if backend in attempted_backends:
                continue
            fallback_queries = _limited_search_queries(rewritten_queries or queries, repair_query_limit)
            before_count = len(results)
            try:
                fallback_results = collect_web_search_results(
                    fallback_queries,
                    search_adapter=deps.fallback_search_adapter(backend),
                    warnings=warnings,
                    backend_label=backend,
                    max_queries=repair_query_limit,
                )
                fallback_results = [{**item, "fallback_candidate": True} for item in fallback_results]
            except Exception as exc:
                fallback_results = []
                warnings.append(f"补充搜索失败（{backend}）：{_friendly_search_error(exc)}")
            results = _dedupe_web_results(fallback_results + results)[:MAX_WEB_CANDIDATES]
            ranked, quality = _rank_and_evaluate_results(query, results, research_plan)
            fallback_attempts.append(
                fallback_attempt(
                    backend=backend,
                    queries=fallback_queries,
                    results=fallback_results,
                    new_result_count=max(len(results) - before_count, 0),
                    trigger="evidence_critic_rejected_all",
                )
            )
            sources, critic_rejected_sources = extract_and_criticize_sources(
                query,
                ranked,
                research_plan,
                extract_adapter,
                warnings=warnings,
                extract_limit=extract_limit,
            )
            if sources:
                warnings.append("首轮来源未通过证据裁判，已通过补充搜索取得可采用来源。")
                break
        if not sources and deps.research_budget_available(8.0):
            repair_queries = _post_critic_repair_queries(query, research_plan, queries, rewritten_queries)
            if repair_queries:
                before_count = len(results)
                try:
                    fallback_results = collect_web_search_results(
                        repair_queries,
                        search_adapter=search_adapter,
                        warnings=warnings,
                        backend_label=primary_backend,
                        max_queries=repair_query_limit,
                    )
                    fallback_results = [{**item, "fallback_candidate": True} for item in fallback_results]
                except Exception as exc:
                    fallback_results = []
                    warnings.append(f"补充搜索失败（{primary_backend or 'primary'}）：{_friendly_search_error(exc)}")
                results = _dedupe_web_results(fallback_results + results)[:MAX_WEB_CANDIDATES]
                ranked, quality = _rank_and_evaluate_results(query, results, research_plan)
                fallback_attempts.append(
                    fallback_attempt(
                        backend=primary_backend or "primary",
                        queries=repair_queries,
                        results=fallback_results,
                        new_result_count=max(len(results) - before_count, 0),
                        trigger="evidence_critic_rejected_all_query_repair",
                    )
                )
                sources, critic_rejected_sources = extract_and_criticize_sources(
                    query,
                    ranked,
                    research_plan,
                    extract_adapter,
                    warnings=warnings,
                    extract_limit=extract_limit,
                )
                if sources:
                    warnings.append("首轮来源未通过证据裁判，已用同后端修复查询取得可采用来源。")
    provider_status = research_provider_status(
        started,
        warnings,
        warning_start,
        quality,
        fallback_attempts,
        sources=sources,
    )
    mark_extraction_evidence_status(sources, critic_rejected_sources, quality)
    missing_required = missing_required_preferred_result(
        query=query,
        ranked=ranked,
        sources=sources,
        critic_rejected_sources=critic_rejected_sources,
        queries=queries,
        research_plan=research_plan,
        quality=quality,
        rewritten_queries=rewritten_queries,
        started=started,
        warnings=warnings,
        warning_start=warning_start,
        fallback_attempts=fallback_attempts,
    )
    if missing_required:
        return missing_required
    mark_sufficient_evidence_status(sources, research_plan, quality)
    if sources and _missing_minimum_reliable_sources(research_plan, sources) and using_default_search and deps.research_budget_available(8.0):
        attempted_backends = attempted_fallback_backends(fallback_attempts)
        for backend in deps.available_fallback_search_backends(primary_backend):
            if backend in attempted_backends:
                continue
            fallback_queries = _limited_search_queries(rewritten_queries or queries, repair_query_limit)
            before_count = len(results)
            try:
                fallback_results = collect_web_search_results(
                    fallback_queries,
                    search_adapter=deps.fallback_search_adapter(backend),
                    warnings=warnings,
                    backend_label=backend,
                    max_queries=repair_query_limit,
                )
                fallback_results = [{**item, "fallback_candidate": True} for item in fallback_results]
            except Exception as exc:
                fallback_results = []
                warnings.append(f"补充搜索失败（{backend}）：{_friendly_search_error(exc)}")
            results = _dedupe_web_results(fallback_results + results)[:MAX_WEB_CANDIDATES]
            ranked, quality = _rank_and_evaluate_results(query, results, research_plan)
            fallback_attempts.append(
                fallback_attempt(
                    backend=backend,
                    queries=fallback_queries,
                    results=fallback_results,
                    new_result_count=max(len(results) - before_count, 0),
                    trigger="insufficient_adopted_sources",
                )
            )
            repaired_sources, critic_rejected_sources = extract_and_criticize_sources(
                query,
                ranked,
                research_plan,
                extract_adapter,
                warnings=warnings,
                extract_limit=extract_limit,
            )
            if _independent_source_count(repaired_sources) > _independent_source_count(sources):
                sources = repaired_sources
                warnings.append("采用来源不足，已通过补充搜索增加独立来源。")
                break
        provider_status = research_provider_status(
            started,
            warnings,
            warning_start,
            quality,
            fallback_attempts,
            sources=sources,
        )
    mark_sufficient_evidence_status(sources, research_plan, quality)
    mark_limited_evidence_status(sources, research_plan, quality, warnings)
    return classified_web_result(
        sources,
        query=query,
        ranked=ranked,
        critic_rejected_sources=critic_rejected_sources,
        queries=queries,
        research_plan=research_plan,
        search_quality=quality,
        rewritten_queries=rewritten_queries,
        provider_status=provider_status,
        adopted_sources=sources,
    )
