"""Web research orchestration for Hermes research."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable

from bridge.research.types import (
    MAX_WEB_CANDIDATES,
    ResearchEventCallback,
    ResearchExtractAdapter,
    ResearchSearchAdapter,
    WEB_FALLBACK_MIN_REMAINING_SECONDS,
)
from bridge.research.utils import (
    _dedupe_web_results,
    _emit_research_event_async,
    _sanitize_evidence_contract,
)
from bridge.research.planner import (
    _limited_search_queries,
    _max_initial_search_queries_for_plan,
    _max_repair_search_queries_for_plan,
    plan_research,
)
from bridge.research.search import (
    _empty_search_quality,
    _independent_source_count,
    _max_extract_urls_for_plan,
    _missing_minimum_reliable_sources,
    _results_include_preferred_domain,
    _should_let_critic_judge_quality_failure,
    _web_sources_from_ranked,
)
from bridge.research.extraction import extract_and_criticize_sources, extract_and_criticize_sources_async
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
    collect_web_search_results_async,
    rewrite_queries_for_low_quality_results,
)
from bridge.research.evidence import _classify_ranked_sources
from bridge.research.synthesis import _trace_sources
from bridge.research.timing import _add_stage_timing, _empty_stage_timings


@dataclass(frozen=True)
class WebResearchDeps:
    default_web_search: ResearchSearchAdapter
    default_web_extract: ResearchExtractAdapter
    default_search_backend: Callable[[], str]
    available_fallback_search_backends: Callable[[str], list[str]]
    fallback_search_adapter: Callable[[str], ResearchSearchAdapter]
    research_budget_available: Callable[[float], bool]


def _fallback_budget_available(deps: WebResearchDeps, warnings: list[str]) -> bool:
    if deps.research_budget_available(WEB_FALLBACK_MIN_REMAINING_SECONDS):
        return True
    warnings.append("研究预算不足，已跳过补充搜索以避免检索链路继续超时。")
    return False


def empty_web_research(
    query: str,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    *,
    research_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    research_plan = research_plan or plan_research(
        query,
        mode="local_web",
        article_context=article_context,
        selection_context=selection_context,
    )
    return {
        "sources": [],
        "retrieved_sources": [],
        "clue_sources": [],
        "rejected_sources": [],
        "queries": research_plan.get("queries") or [],
        "research_plan": research_plan,
        "search_quality": _empty_search_quality("not_searched"),
        "rewritten_queries": [],
        "provider_status": {"search_elapsed_ms": 0, "stage_timings": _empty_stage_timings(), "warnings": []},
    }


from bridge.research.orchestration_sync import run_web_research


async def run_web_research_async(
    query: str,
    warnings: list[str],
    *,
    research_plan: dict[str, Any] | None = None,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    search_adapter: ResearchSearchAdapter | None,
    extract_adapter: ResearchExtractAdapter | None,
    event_callback: ResearchEventCallback | None = None,
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
    stage_timings = _empty_stage_timings()
    fallback_attempts: list[dict[str, Any]] = []
    query_statuses: list[dict[str, Any]] = []
    primary_backend = deps.default_search_backend() if using_default_search else ""
    search_started = time.monotonic()
    direct_candidates = (
        await asyncio.to_thread(_direct_retrieval_candidates, query, research_plan, existing_results=[])
        if using_default_search
        else []
    )
    results = await collect_web_search_results_async(
        queries,
        search_adapter=search_adapter,
        warnings=warnings,
        event_callback=event_callback,
        backend_label=primary_backend,
        max_queries=initial_query_limit,
        query_statuses=query_statuses,
    )
    if using_default_search:
        results = _dedupe_web_results(direct_candidates + results)[:MAX_WEB_CANDIDATES]
        mentioned_url_candidates = await asyncio.to_thread(_direct_mentioned_url_candidates, results, research_plan)
        if mentioned_url_candidates:
            results = _dedupe_web_results(mentioned_url_candidates + results)[:MAX_WEB_CANDIDATES]
    _add_stage_timing(stage_timings, "search_ms", search_started)
    if using_default_search:
        preferred_domain_queries = _preferred_domain_limited_queries(queries, research_plan.get("source_preferences") or {})
        if (
            preferred_domain_queries
            and not _results_include_preferred_domain(results, research_plan.get("source_preferences") or {})
            and _fallback_budget_available(deps, warnings)
        ):
            attempted_backends = {primary_backend}
            for backend in deps.available_fallback_search_backends(primary_backend):
                if backend in attempted_backends:
                    continue
                await _emit_research_event_async(
                    event_callback,
                    "web_fallback_start",
                    backend=backend,
                    reason="preferred_domain_missing",
                    queries=preferred_domain_queries,
                )
                before_count = len(results)
                fallback_started = time.monotonic()
                try:
                    preferred_results = await collect_web_search_results_async(
                        preferred_domain_queries,
                        search_adapter=deps.fallback_search_adapter(backend),
                        warnings=warnings,
                        event_callback=event_callback,
                        backend_label=backend,
                        max_queries=repair_query_limit,
                        query_statuses=query_statuses,
                    )
                    preferred_results = [{**item, "fallback_candidate": True} for item in preferred_results]
                except Exception as exc:
                    preferred_results = []
                    warnings.append(f"优先域名补充搜索失败（{backend}）：{_friendly_search_error(exc)}")
                finally:
                    _add_stage_timing(stage_timings, "fallback_ms", fallback_started)
                results = _dedupe_web_results(preferred_results + results)[:MAX_WEB_CANDIDATES]
                attempt = fallback_attempt(
                    backend=backend,
                    queries=preferred_domain_queries,
                    results=preferred_results,
                    new_result_count=max(len(results) - before_count, 0),
                    trigger="preferred_domain_missing",
                )
                fallback_attempts.append(attempt)
                await _emit_research_event_async(
                    event_callback,
                    "web_fallback_done",
                    backend=backend,
                    result_count=len(preferred_results),
                    new_result_count=attempt["new_result_count"],
                    search_quality={},
                    web_sources=_trace_sources(_web_sources_from_ranked(results, {})),
                )
                if _results_include_preferred_domain(results, research_plan.get("source_preferences") or {}):
                    break
                attempted_backends.add(backend)
    ranked, quality = _rank_and_evaluate_results(query, results, research_plan)
    await _emit_research_event_async(
        event_callback,
        "web_ranked",
        search_quality=quality,
        web_sources=_trace_sources(_web_sources_from_ranked(ranked, {})),
    )
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
            await _emit_research_event_async(event_callback, "web_rewrite_start", queries=rewritten_queries)
            search_started = time.monotonic()
            rewritten_results = await collect_web_search_results_async(
                rewritten_queries,
                search_adapter=search_adapter,
                warnings=warnings,
                event_callback=event_callback,
                backend_label=primary_backend,
                max_queries=repair_query_limit,
                query_statuses=query_statuses,
            )
            _add_stage_timing(stage_timings, "search_ms", search_started)
            results = _dedupe_web_results(rewritten_results + results)
            ranked, quality = _rank_and_evaluate_results(query, results, research_plan)
            quality["rewrite_attempted"] = True
            quality["rewritten_queries"] = rewritten_queries
            await _emit_research_event_async(
                event_callback,
                "web_ranked",
                search_quality=quality,
                web_sources=_trace_sources(_web_sources_from_ranked(ranked, {})),
                rewritten_queries=rewritten_queries,
            )
    if (
        using_default_search
        and (quality.get("retrieval_health") or {}).get("needs_recall_repair")
        and _fallback_budget_available(deps, warnings)
    ):
        fallback_queries = _limited_search_queries(rewritten_queries or queries, repair_query_limit)
        for backend in deps.available_fallback_search_backends(primary_backend):
            trigger = (quality.get("retrieval_health") or {}).get("repair_reason") or ""
            await _emit_research_event_async(
                event_callback,
                "web_fallback_start",
                backend=backend,
                reason=trigger,
                queries=fallback_queries,
            )
            before_count = len(results)
            fallback_started = time.monotonic()
            try:
                fallback_results = await collect_web_search_results_async(
                    fallback_queries,
                    search_adapter=deps.fallback_search_adapter(backend),
                    warnings=warnings,
                    event_callback=event_callback,
                    backend_label=backend,
                    max_queries=repair_query_limit,
                    query_statuses=query_statuses,
                )
                fallback_results = [{**item, "fallback_candidate": True} for item in fallback_results]
            except Exception as exc:
                fallback_results = []
                warnings.append(f"补充搜索失败（{backend}）：{_friendly_search_error(exc)}")
            finally:
                _add_stage_timing(stage_timings, "fallback_ms", fallback_started)
            results = _dedupe_web_results(fallback_results + results)[:MAX_WEB_CANDIDATES]
            ranked, quality = _rank_and_evaluate_results(query, results, research_plan)
            attempt = fallback_attempt(
                backend=backend,
                queries=fallback_queries,
                results=fallback_results,
                new_result_count=max(len(results) - before_count, 0),
                trigger=trigger,
            )
            fallback_attempts.append(attempt)
            await _emit_research_event_async(
                event_callback,
                "web_fallback_done",
                backend=backend,
                result_count=len(fallback_results),
                new_result_count=attempt["new_result_count"],
                search_quality=quality,
                web_sources=_trace_sources(_web_sources_from_ranked(ranked, {})),
            )
            if quality.get("passed") and not (quality.get("retrieval_health") or {}).get("needs_recall_repair"):
                break
    provider_status = research_provider_status(
        started,
        warnings,
        warning_start,
        quality,
        fallback_attempts,
        stage_timings=stage_timings,
        query_statuses=query_statuses,
    )
    classified = _classify_ranked_sources(
        query,
        ranked,
        source_preferences=research_plan.get("source_preferences") or {},
        subject=research_plan.get("subject") or {},
        evidence_contract=research_plan.get("evidence_contract") or {},
    )
    await _emit_research_event_async(
        event_callback,
        "web_classified",
        candidate_sources=_trace_sources(classified["candidate_sources"]),
        clue_sources=_trace_sources(classified["clue_sources"]),
        rejected_sources=_trace_sources(classified["rejected_sources"]),
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
            stage_timings=stage_timings,
            query_statuses=query_statuses,
        )
        return web_research_result(
            [],
            classified=classified,
            queries=queries,
            research_plan=research_plan,
            search_quality=quality,
            rewritten_queries=rewritten_queries,
            provider_status=provider_status,
        )
    await _emit_research_event_async(
        event_callback,
        "web_extract_start",
        web_sources=_trace_sources(_web_sources_from_ranked(ranked[:_max_extract_urls_for_plan(research_plan)], {})),
    )
    extract_limit = _max_extract_urls_for_plan(research_plan)
    sources, critic_rejected_sources = await extract_and_criticize_sources_async(
        query,
        ranked,
        research_plan,
        extract_adapter,
        warnings,
        extract_limit=extract_limit,
        event_callback=event_callback,
        stage_timings=stage_timings,
    )
    if (
        using_default_search
        and critic_rejected_sources
        and not sources
        and _fallback_budget_available(deps, warnings)
    ):
        attempted_backends = attempted_fallback_backends(fallback_attempts)
        for backend in deps.available_fallback_search_backends(primary_backend):
            if backend in attempted_backends:
                continue
            fallback_queries = _limited_search_queries(rewritten_queries or queries, repair_query_limit)
            await _emit_research_event_async(
                event_callback,
                "web_fallback_start",
                backend=backend,
                reason="evidence_critic_rejected_all",
                queries=fallback_queries,
            )
            before_count = len(results)
            fallback_started = time.monotonic()
            try:
                fallback_results = await collect_web_search_results_async(
                    fallback_queries,
                    search_adapter=deps.fallback_search_adapter(backend),
                    warnings=warnings,
                    event_callback=event_callback,
                    backend_label=backend,
                    max_queries=repair_query_limit,
                    query_statuses=query_statuses,
                )
                fallback_results = [{**item, "fallback_candidate": True} for item in fallback_results]
            except Exception as exc:
                fallback_results = []
                warnings.append(f"补充搜索失败（{backend}）：{_friendly_search_error(exc)}")
            finally:
                _add_stage_timing(stage_timings, "fallback_ms", fallback_started)
            results = _dedupe_web_results(fallback_results + results)[:MAX_WEB_CANDIDATES]
            ranked, quality = _rank_and_evaluate_results(query, results, research_plan)
            attempt = fallback_attempt(
                backend=backend,
                queries=fallback_queries,
                results=fallback_results,
                new_result_count=max(len(results) - before_count, 0),
                trigger="evidence_critic_rejected_all",
            )
            fallback_attempts.append(attempt)
            await _emit_research_event_async(
                event_callback,
                "web_fallback_done",
                backend=backend,
                result_count=len(fallback_results),
                new_result_count=attempt["new_result_count"],
                search_quality=quality,
                web_sources=_trace_sources(_web_sources_from_ranked(ranked, {})),
            )
            sources, critic_rejected_sources = await extract_and_criticize_sources_async(
                query,
                ranked,
                research_plan,
                extract_adapter,
                warnings=warnings,
                extract_limit=extract_limit,
                event_callback=event_callback,
                stage_timings=stage_timings,
            )
            if sources:
                warnings.append("首轮来源未通过证据裁判，已通过补充搜索取得可采用来源。")
                break
        if not sources and _fallback_budget_available(deps, warnings):
            repair_queries = _post_critic_repair_queries(query, research_plan, queries, rewritten_queries)
            if repair_queries:
                await _emit_research_event_async(
                    event_callback,
                    "web_fallback_start",
                    backend=primary_backend or "primary",
                    reason="evidence_critic_rejected_all_query_repair",
                    queries=repair_queries,
                )
                before_count = len(results)
                fallback_started = time.monotonic()
                try:
                    fallback_results = await collect_web_search_results_async(
                        repair_queries,
                        search_adapter=search_adapter,
                        warnings=warnings,
                        event_callback=event_callback,
                        backend_label=primary_backend,
                        max_queries=repair_query_limit,
                        query_statuses=query_statuses,
                    )
                    fallback_results = [{**item, "fallback_candidate": True} for item in fallback_results]
                except Exception as exc:
                    fallback_results = []
                    warnings.append(f"补充搜索失败（{primary_backend or 'primary'}）：{_friendly_search_error(exc)}")
                finally:
                    _add_stage_timing(stage_timings, "fallback_ms", fallback_started)
                results = _dedupe_web_results(fallback_results + results)[:MAX_WEB_CANDIDATES]
                ranked, quality = _rank_and_evaluate_results(query, results, research_plan)
                attempt = fallback_attempt(
                    backend=primary_backend or "primary",
                    queries=repair_queries,
                    results=fallback_results,
                    new_result_count=max(len(results) - before_count, 0),
                    trigger="evidence_critic_rejected_all_query_repair",
                )
                fallback_attempts.append(attempt)
                await _emit_research_event_async(
                    event_callback,
                    "web_fallback_done",
                    backend=primary_backend or "primary",
                    result_count=len(fallback_results),
                    new_result_count=attempt["new_result_count"],
                    search_quality=quality,
                    web_sources=_trace_sources(_web_sources_from_ranked(ranked, {})),
                )
                sources, critic_rejected_sources = await extract_and_criticize_sources_async(
                    query,
                    ranked,
                    research_plan,
                    extract_adapter,
                    warnings=warnings,
                    extract_limit=extract_limit,
                    event_callback=event_callback,
                    stage_timings=stage_timings,
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
        stage_timings=stage_timings,
        query_statuses=query_statuses,
    )
    await _emit_research_event_async(
        event_callback,
        "web_critic_done",
        web_sources=_trace_sources(sources),
        rejected_sources=_trace_sources(critic_rejected_sources),
    )
    await _emit_research_event_async(
        event_callback,
        "web_extract_done",
        provider_status=provider_status,
        web_sources=_trace_sources(sources),
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
        stage_timings=stage_timings,
        query_statuses=query_statuses,
    )
    if missing_required:
        await _emit_research_event_async(
            event_callback,
            "web_classified",
            candidate_sources=_trace_sources(missing_required["retrieved_sources"]),
            clue_sources=_trace_sources(missing_required["clue_sources"]),
            rejected_sources=_trace_sources(missing_required["rejected_sources"]),
        )
        return missing_required
    mark_sufficient_evidence_status(sources, research_plan, quality)
    if (
        sources
        and _missing_minimum_reliable_sources(research_plan, sources)
        and using_default_search
        and _fallback_budget_available(deps, warnings)
    ):
        attempted_backends = attempted_fallback_backends(fallback_attempts)
        for backend in deps.available_fallback_search_backends(primary_backend):
            if backend in attempted_backends:
                continue
            fallback_queries = _limited_search_queries(rewritten_queries or queries, repair_query_limit)
            await _emit_research_event_async(
                event_callback,
                "web_fallback_start",
                backend=backend,
                reason="insufficient_adopted_sources",
                queries=fallback_queries,
            )
            before_count = len(results)
            fallback_started = time.monotonic()
            try:
                fallback_results = await collect_web_search_results_async(
                    fallback_queries,
                    search_adapter=deps.fallback_search_adapter(backend),
                    warnings=warnings,
                    event_callback=event_callback,
                    backend_label=backend,
                    max_queries=repair_query_limit,
                    query_statuses=query_statuses,
                )
                fallback_results = [{**item, "fallback_candidate": True} for item in fallback_results]
            except Exception as exc:
                fallback_results = []
                warnings.append(f"补充搜索失败（{backend}）：{_friendly_search_error(exc)}")
            finally:
                _add_stage_timing(stage_timings, "fallback_ms", fallback_started)
            results = _dedupe_web_results(fallback_results + results)[:MAX_WEB_CANDIDATES]
            ranked, quality = _rank_and_evaluate_results(query, results, research_plan)
            attempt = fallback_attempt(
                backend=backend,
                queries=fallback_queries,
                results=fallback_results,
                new_result_count=max(len(results) - before_count, 0),
                trigger="insufficient_adopted_sources",
            )
            fallback_attempts.append(attempt)
            await _emit_research_event_async(
                event_callback,
                "web_fallback_done",
                backend=backend,
                result_count=len(fallback_results),
                new_result_count=attempt["new_result_count"],
                search_quality=quality,
                web_sources=_trace_sources(_web_sources_from_ranked(ranked, {})),
            )
            repaired_sources, critic_rejected_sources = await extract_and_criticize_sources_async(
                query,
                ranked,
                research_plan,
                extract_adapter,
                warnings=warnings,
                extract_limit=extract_limit,
                event_callback=event_callback,
                stage_timings=stage_timings,
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
            stage_timings=stage_timings,
            query_statuses=query_statuses,
        )
    mark_sufficient_evidence_status(sources, research_plan, quality)
    mark_limited_evidence_status(sources, research_plan, quality, warnings)
    result = classified_web_result(
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
    await _emit_research_event_async(
        event_callback,
        "web_classified",
        candidate_sources=_trace_sources(result["retrieved_sources"]),
        clue_sources=_trace_sources(result["clue_sources"]),
        rejected_sources=_trace_sources(result["rejected_sources"]),
    )
    return result
