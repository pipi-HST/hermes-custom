"""Lightweight web QA retrieval path.

This module intentionally avoids the legacy evidence critic pipeline.  The
contract is simple: code bounds resources and validates source plumbing; the
answer model decides semantic usefulness from bounded search summaries and
extracted page text.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any

from agents.capture_agent import load_config
from bridge.prompt_safety import sanitize_untrusted_text

from bridge.research.adapters import _default_search_backend
from bridge.research.results import research_provider_status, web_research_result
from bridge.research.search import _independent_source_count
from bridge.research.search_infra import (
    collect_web_search_results,
    collect_web_search_results_async,
    _direct_mentioned_url_candidates,
    prefilter_web_sources,
)
from bridge.research.synthesis import _call_llm, _trace_sources
from bridge.research.timing import _add_stage_timing, _empty_stage_timings
from bridge.research.types import (
    MAX_WEB_QUERIES,
    MAX_WEB_CANDIDATES,
    ResearchEventCallback,
    ResearchExtractAdapter,
    ResearchSearchAdapter,
)
from bridge.research.utils import (
    _clean_web_url,
    _clip_text,
    _dedupe_web_results,
    _domain,
    _emit_research_event_async,
    _json_payload,
    _parse_web_extract,
    _research_budget_available,
    _sanitize_evidence_contract,
    _sanitize_source_preferences,
    _stage_timeout,
    _unique,
    _unique_queries,
)

LIGHT_MAX_CANDIDATES = 20
LIGHT_SIMPLE_CANDIDATES = 10
WEB_ASSIST_MAX_CANDIDATES = 12
LIGHT_RESULTS_PER_QUERY = 8
WEB_ASSIST_RESULTS_PER_QUERY = 6
LIGHT_SYNTHESIS_SOURCE_LIMIT = 8
LIGHT_EXTRACT_TIMEOUT_SECONDS = 12.0
LIGHT_RICH_SUMMARY_CHARS = 40


def run_light_web_qa(
    query: str,
    warnings: list[str],
    *,
    research_plan: dict[str, Any],
    search_adapter: ResearchSearchAdapter,
    extract_adapter: ResearchExtractAdapter,
    promote_mentioned_urls: bool = False,
    path_label: str = "light_web_qa",
) -> dict[str, Any]:
    """Run the synchronous lightweight web QA retrieval path."""
    started = time.monotonic()
    warning_start = len(warnings)
    stage_timings = _empty_stage_timings()
    queries = _light_queries(query, research_plan)
    query_statuses: list[dict[str, Any]] = []
    search_started = time.monotonic()
    results = collect_web_search_results(
        queries,
        search_adapter=_limit_wrapped_search_adapter(search_adapter, path_label=path_label),
        warnings=warnings,
        backend_label=_default_search_backend(),
        max_queries=min(len(queries), _light_query_limit(path_label)),
        query_statuses=query_statuses,
    )
    if promote_mentioned_urls:
        results = _promote_mentioned_url_candidates(query, results, research_plan)
    _add_stage_timing(stage_timings, "search_ms", search_started)
    return _finish_light_web_qa(
        query,
        warnings,
        research_plan=research_plan,
        queries=queries,
        raw_results=results,
        extract_adapter=extract_adapter,
        started=started,
        warning_start=warning_start,
        stage_timings=stage_timings,
        query_statuses=query_statuses,
        path_label=path_label,
    )


async def run_light_web_qa_async(
    query: str,
    warnings: list[str],
    *,
    research_plan: dict[str, Any],
    search_adapter: ResearchSearchAdapter,
    extract_adapter: ResearchExtractAdapter,
    event_callback: ResearchEventCallback | None = None,
    promote_mentioned_urls: bool = False,
    path_label: str = "light_web_qa",
) -> dict[str, Any]:
    """Run the async lightweight web QA retrieval path."""
    started = time.monotonic()
    warning_start = len(warnings)
    stage_timings = _empty_stage_timings()
    queries = _light_queries(query, research_plan)
    query_statuses: list[dict[str, Any]] = []
    search_started = time.monotonic()
    results = await collect_web_search_results_async(
        queries,
        search_adapter=_limit_wrapped_search_adapter(search_adapter, path_label=path_label),
        warnings=warnings,
        event_callback=event_callback,
        backend_label=_default_search_backend(),
        max_queries=min(len(queries), _light_query_limit(path_label)),
        query_statuses=query_statuses,
    )
    if promote_mentioned_urls:
        results = await asyncio.to_thread(_promote_mentioned_url_candidates, query, results, research_plan)
    _add_stage_timing(stage_timings, "search_ms", search_started)
    return await _finish_light_web_qa_async(
        query,
        warnings,
        research_plan=research_plan,
        queries=queries,
        raw_results=results,
        extract_adapter=extract_adapter,
        started=started,
        warning_start=warning_start,
        stage_timings=stage_timings,
        query_statuses=query_statuses,
        event_callback=event_callback,
        path_label=path_label,
    )


async def _finish_light_web_qa_async(
    query: str,
    warnings: list[str],
    *,
    research_plan: dict[str, Any],
    queries: list[str],
    raw_results: list[dict[str, Any]],
    extract_adapter: ResearchExtractAdapter,
    started: float,
    warning_start: int,
    stage_timings: dict[str, Any],
    query_statuses: list[dict[str, Any]],
    event_callback: ResearchEventCallback | None = None,
    path_label: str = "light_web_qa",
) -> dict[str, Any]:
    candidate_limit = _light_candidate_limit(research_plan, path_label=path_label)
    candidates = _light_candidates(query, raw_results, research_plan, limit=candidate_limit)
    quality = _light_search_quality(candidates, research_plan)
    await _emit_research_event_async(
        event_callback,
        "web_ranked",
        search_quality=quality,
        web_sources=_trace_sources(candidates),
    )
    selected, need_extract_refs = await asyncio.to_thread(
        _select_candidates,
        query,
        candidates,
        research_plan,
        warnings,
        path_label,
    )
    selected = selected[:_light_extract_limit(research_plan, path_label=path_label)]
    urls = _extract_urls_for_selected(query, selected, research_plan, need_extract_refs)
    extracted_by_url: dict[str, str] = {}
    extract_failures: list[str] = []
    if urls and _research_budget_available(3.0):
        await _emit_research_event_async(event_callback, "web_extract_start", urls=urls, web_sources=_trace_sources(selected))
        extract_started = time.monotonic()
        try:
            raw_extract = await _call_extract_adapter_async(extract_adapter, urls)
            extracted_by_url = _normalized_extract_map(_parse_web_extract(raw_extract))
            extract_failures = _extract_failure_urls(raw_extract)
        except Exception as exc:
            warnings.append(f"网页正文抽取失败，已保留搜索摘要回答：{exc}")
            extract_failures = urls
        finally:
            _add_stage_timing(stage_timings, "extract_ms", extract_started)
        preview_sources = _build_light_sources(selected, extracted_by_url, extract_failures)
        await _emit_research_event_async(
            event_callback,
            "web_extract_done",
            urls=urls,
            result_count=len(preview_sources),
            web_sources=_trace_sources(preview_sources),
        )
    sources = _build_light_sources(selected, extracted_by_url, extract_failures)
    return _assemble_light_web_result(
        query,
        warnings,
        research_plan=research_plan,
        queries=queries,
        raw_results=raw_results,
        candidates=candidates,
        sources=sources,
        quality=quality,
        started=started,
        warning_start=warning_start,
        stage_timings=stage_timings,
        query_statuses=query_statuses,
        read_pages=len(urls),
        path_label=path_label,
    )


def _finish_light_web_qa(
    query: str,
    warnings: list[str],
    *,
    research_plan: dict[str, Any],
    queries: list[str],
    raw_results: list[dict[str, Any]],
    extract_adapter: ResearchExtractAdapter,
    started: float,
    warning_start: int,
    stage_timings: dict[str, Any],
    query_statuses: list[dict[str, Any]],
    event_callback: ResearchEventCallback | None = None,
    path_label: str = "light_web_qa",
) -> dict[str, Any]:
    candidate_limit = _light_candidate_limit(research_plan, path_label=path_label)
    candidates = _light_candidates(query, raw_results, research_plan, limit=candidate_limit)
    quality = _light_search_quality(candidates, research_plan)
    _emit_from_thread(
        event_callback,
        "web_ranked",
        search_quality=quality,
        web_sources=_trace_sources(candidates),
    )
    selected, need_extract_refs = _select_candidates(query, candidates, research_plan, warnings, path_label)
    read_limit = _light_extract_limit(research_plan, path_label=path_label)
    selected = selected[:read_limit]
    extracted_by_url: dict[str, str] = {}
    extract_failures: list[str] = []
    urls = _extract_urls_for_selected(query, selected, research_plan, need_extract_refs)
    if urls and _research_budget_available(3.0):
        _emit_from_thread(event_callback, "web_extract_start", urls=urls)
        extract_started = time.monotonic()
        try:
            raw_extract = _call_extract_adapter_sync(extract_adapter, urls)
            extracted_by_url = _normalized_extract_map(_parse_web_extract(raw_extract))
            extract_failures = _extract_failure_urls(raw_extract)
        except Exception as exc:
            warnings.append(f"网页正文抽取失败，已保留搜索摘要回答：{exc}")
            extract_failures = urls
        finally:
            _add_stage_timing(stage_timings, "extract_ms", extract_started)
        _emit_from_thread(
            event_callback,
            "web_extract_done",
            urls=urls,
            extracted_count=len(extracted_by_url),
        )
    sources = _build_light_sources(selected, extracted_by_url, extract_failures)
    return _assemble_light_web_result(
        query,
        warnings,
        research_plan=research_plan,
        queries=queries,
        raw_results=raw_results,
        candidates=candidates,
        sources=sources,
        quality=quality,
        started=started,
        warning_start=warning_start,
        stage_timings=stage_timings,
        query_statuses=query_statuses,
        read_pages=len(urls),
        path_label=path_label,
    )


def _assemble_light_web_result(
    query: str,
    warnings: list[str],
    *,
    research_plan: dict[str, Any],
    queries: list[str],
    raw_results: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    quality: dict[str, Any],
    started: float,
    warning_start: int,
    stage_timings: dict[str, Any],
    query_statuses: list[dict[str, Any]],
    read_pages: int,
    path_label: str = "light_web_qa",
) -> dict[str, Any]:
    if raw_results and not sources:
        summary_candidates = _summary_fallback_candidates(query, candidates, research_plan)
        if summary_candidates:
            warnings.append("联网搜索已有候选结果，但页面正文不可用；已保留搜索摘要作为有限材料。")
            sources = _build_light_sources(summary_candidates, {}, [])
            quality["evidence_status"] = "limited"
            quality["reason"] = "summary_only_sources"
        else:
            quality["evidence_status"] = "insufficient"
            quality["reason"] = "weak_or_unusable_summary_sources"
    provider_status = research_provider_status(
        started,
        warnings,
        warning_start,
        quality,
        [],
        sources=sources,
        stage_timings=stage_timings,
        query_statuses=query_statuses,
    )
    provider_status["path"] = path_label
    backend_name = str(_default_search_backend() or "")
    # Silent degradation is the dangerous case: the user must be able to tell
    # that the answer came from a fallback search backend, not DeepSeek.
    provider_status["search_backend"] = backend_name or "unknown"
    provider_status["search_backend_is_deepseek"] = backend_name == "deepseek"
    provider_status["candidate_count"] = len(candidates)
    provider_status["read_pages"] = read_pages
    provider_status["used_sources"] = len(sources)
    provider_status["fallback_search"] = False
    classified = {
        "candidate_sources": candidates,
        "clue_sources": [item for item in candidates if item.get("url") not in {source.get("url") for source in sources}],
        "rejected_sources": [],
    }
    return web_research_result(
        sources,
        classified=classified,
        queries=queries,
        research_plan=research_plan,
        search_quality=quality,
        rewritten_queries=[],
        provider_status=provider_status,
    )


def _light_queries(query: str, research_plan: dict[str, Any]) -> list[str]:
    planned = [str(item or "").strip() for item in research_plan.get("queries") or []]
    queries = _unique_queries([*planned, query])
    return queries[:MAX_WEB_QUERIES] or [query]


def _light_query_limit(path_label: str = "light_web_qa") -> int:
    if path_label == "web_assist":
        return min(3, MAX_WEB_QUERIES)
    return MAX_WEB_QUERIES


def _limit_wrapped_search_adapter(
    search_adapter: ResearchSearchAdapter,
    *,
    path_label: str = "light_web_qa",
) -> ResearchSearchAdapter:
    def _wrapped(search_query: str, limit: int) -> str | dict[str, Any]:
        per_query = WEB_ASSIST_RESULTS_PER_QUERY if path_label == "web_assist" else LIGHT_RESULTS_PER_QUERY
        return search_adapter(search_query, min(max(int(limit or per_query), 1), per_query))

    return _wrapped


def _promote_mentioned_url_candidates(
    query: str,
    results: list[dict[str, Any]],
    research_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    mentioned = _direct_mentioned_url_candidates(results, research_plan)
    if not mentioned:
        return results
    return _dedupe_web_results(mentioned + results)[:MAX_WEB_CANDIDATES]


def _light_candidate_limit(research_plan: dict[str, Any], *, path_label: str = "light_web_qa") -> int:
    if path_label == "web_assist":
        return WEB_ASSIST_MAX_CANDIDATES
    if research_plan.get("answer_depth") == "brief" and not research_plan.get("freshness_sensitive"):
        return LIGHT_SIMPLE_CANDIDATES
    return LIGHT_MAX_CANDIDATES


def _light_extract_limit(research_plan: dict[str, Any], *, path_label: str = "light_web_qa") -> int:
    if path_label == "web_assist":
        if research_plan.get("risk_level") == "high" or research_plan.get("freshness_sensitive"):
            return 2
        if research_plan.get("answer_depth") == "detailed":
            return 2
        return 1
    if research_plan.get("answer_depth") == "brief" and not research_plan.get("freshness_sensitive"):
        return 3
    if research_plan.get("answer_depth") == "detailed" or research_plan.get("freshness_sensitive") or research_plan.get("risk_level") == "high":
        return 6
    return 3


def _extract_urls_for_selected(
    query: str,
    selected: list[dict[str, Any]],
    research_plan: dict[str, Any],
    need_extract_refs: set[str],
) -> list[str]:
    if not selected:
        return []
    if _local_concept_summary_mode(query, research_plan):
        return []
    if _summary_only_is_enough(query, selected, research_plan):
        # Fetch at least the top page: a summary-only answer has no
        # "正文已抓取" source, which starves the evidence contract and makes
        # every citation unverifiable.  One fetch keeps latency bounded while
        # giving the answer a reliable anchor.
        top_url = next(
            (str(item.get("url")) for item in selected if item.get("url")), ""
        )
        return [top_url] if top_url else []
    if need_extract_refs:
        return [
            str(item.get("url") or "")
            for item in selected
            if item.get("url") and str(item.get("ref") or "").upper() in need_extract_refs
        ]
    if _summary_only_is_enough(query, selected, research_plan):
        return []
    return [str(item.get("url") or "") for item in selected if item.get("url")]


def _local_concept_summary_mode(query: str, research_plan: dict[str, Any]) -> bool:
    if research_plan.get("mode") != "local_web":
        return False
    if research_plan.get("risk_level") == "high" or research_plan.get("freshness_sensitive"):
        return False
    if research_plan.get("answer_depth") == "detailed":
        return False
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    if not contract.get("allow_summary_only", True):
        return False
    text = f"{query} {research_plan.get('intent_label') or ''} {(research_plan.get('subject') or {}).get('type_hint') or ''}"
    return bool(
        re.search(
            r"什么是|是什么|什么意思|关系|区别|差异|介绍|解释|concept|explainer|who\s+is|what\s+is|explain",
            text,
            re.I,
        )
    )


async def _call_extract_adapter_async(
    extract_adapter: ResearchExtractAdapter,
    urls: list[str],
) -> str | dict[str, Any]:
    timeout_seconds = _light_extract_timeout()
    raw_extract = await asyncio.wait_for(asyncio.to_thread(extract_adapter, urls), timeout=timeout_seconds)
    if asyncio.iscoroutine(raw_extract):
        raw_extract = await asyncio.wait_for(raw_extract, timeout=timeout_seconds)
    return raw_extract


def _call_extract_adapter_sync(
    extract_adapter: ResearchExtractAdapter,
    urls: list[str],
) -> str | dict[str, Any]:
    timeout_seconds = _light_extract_timeout()
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(extract_adapter, urls)
    try:
        raw_extract = future.result(timeout=timeout_seconds)
    except FuturesTimeoutError as exc:
        future.cancel()
        raise TimeoutError(f"web extract timed out after {timeout_seconds:.1f}s") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    if asyncio.iscoroutine(raw_extract):
        raw_extract = _run_coro_sync(asyncio.wait_for(raw_extract, timeout=timeout_seconds))
    return raw_extract


def _light_extract_timeout() -> float:
    return max(1.0, _stage_timeout(LIGHT_EXTRACT_TIMEOUT_SECONDS, reserve_seconds=5.0))


def _summary_only_is_enough(query: str, selected: list[dict[str, Any]], research_plan: dict[str, Any]) -> bool:
    # Only DeepSeek's own search API returns query-conditioned long summaries;
    # a fallback backend's short snippet is not evidence and must not skip fetch.
    if str(_default_search_backend() or "") != "deepseek":
        return False
    if research_plan.get("risk_level") == "high" or research_plan.get("freshness_sensitive"):
        return False
    if research_plan.get("answer_depth") == "detailed":
        return False
    text = f"{query} {research_plan.get('intent_label') or ''}"
    if not re.search(
        r"什么是|是什么|什么意思|怎么理解|核心概念|基本原理|关系|区别|差异|对比|优缺点|介绍|解释|"
        r"who\s+is|what\s+is|relationship|difference|explain",
        text,
        re.I,
    ):
        return False
    rich_summaries = [
        str(item.get("description") or item.get("excerpt") or "").strip()
        for item in selected
        if len(str(item.get("description") or item.get("excerpt") or "").strip()) >= LIGHT_RICH_SUMMARY_CHARS
    ]
    if len(rich_summaries) >= 2:
        return True
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    if int(contract.get("minimum_independent_sources") or 1) <= 1 and rich_summaries:
        return True
    return any(
        len(str(item.get("description") or item.get("excerpt") or "").strip()) >= LIGHT_RICH_SUMMARY_CHARS
        and not _is_light_weak_source(item)
        and str(item.get("source_authority") or "") in {"official", "preferred", "trusted"}
        for item in selected
    )


def _light_candidates(
    query: str,
    raw_results: list[dict[str, Any]],
    research_plan: dict[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    deduped = _dedupe_web_results(raw_results)[:limit]
    ranked = prefilter_web_sources(deduped, source_preferences=research_plan.get("source_preferences") or {})
    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(ranked[:limit], 1):
        candidates.append(
            {
                **item,
                "ref": f"W{index}",
                "type": "web",
                "id": item.get("url") or f"web-{index}",
                "domain": item.get("domain") or _domain(item.get("url") or ""),
                "excerpt": str(item.get("description") or "").strip(),
                "extract_status": "candidate",
                "source_role": "candidate",
                "evidence_role": "candidate",
                "source_authority": _light_source_authority(item),
                "allowed_usage": ["background", "fact"],
                "confidence": "medium",
                "supports_claims": ["answer_user_question"],
                "claim_support": [
                    {
                        "claim_id": "answer_user_question",
                        "support": "yes",
                        "reason": "LLM selected within lightweight web QA candidate pool",
                    }
                ],
            }
        )
    return candidates


def _light_source_authority(source: dict[str, Any]) -> str:
    tags = set(str(item) for item in source.get("quality_tags") or [])
    if "mentioned_subject_domain" in tags:
        return "official"
    if "planner_primary_source" in tags or "planner_preferred_domain" in tags:
        return "preferred"
    if "trusted_domain" in tags:
        return "trusted"
    if "weak_secondary_source" in tags:
        return "secondary"
    return "web"


def _select_candidates(
    query: str,
    candidates: list[dict[str, Any]],
    research_plan: dict[str, Any],
    warnings: list[str],
    path_label: str = "light_web_qa",
) -> tuple[list[dict[str, Any]], set[str]]:
    if not candidates:
        return [], set()
    read_limit = _light_extract_limit(research_plan, path_label=path_label)
    selection = _candidate_selection_plan(query, candidates, research_plan, read_limit)
    selected_refs = selection["selected_refs"]
    if selected_refs:
        by_ref = {str(item.get("ref") or "").upper(): item for item in candidates}
        selected = [by_ref[ref] for ref in selected_refs if ref in by_ref]
        if selected:
            selected = _apply_weak_source_policy(query, selected, candidates, research_plan)
            return selected, set(selection["need_extract_refs"])
    selected = _apply_weak_source_policy(query, candidates[:read_limit], candidates, research_plan)
    return selected, set()


def _candidate_selection_plan(
    query: str,
    candidates: list[dict[str, Any]],
    research_plan: dict[str, Any],
    read_limit: int,
) -> dict[str, list[str]]:
    raw = _llm_selected_refs(query, candidates, research_plan, read_limit)
    if isinstance(raw, dict):
        selected = _normalize_ref_list(raw.get("selected_refs") or raw.get("selected") or [], read_limit)
        need_extract = _normalize_ref_list(raw.get("need_extract_refs") or [], read_limit)
        summary_only = _normalize_ref_list(raw.get("answerable_from_summary_refs") or [], read_limit)
        if not selected:
            selected = _unique([*need_extract, *summary_only])[:read_limit]
        return {
            "selected_refs": selected,
            "need_extract_refs": [ref for ref in need_extract if ref in set(selected)],
            "answerable_from_summary_refs": [ref for ref in summary_only if ref in set(selected)],
        }
    selected = _normalize_ref_list(raw or [], read_limit)
    return {
        "selected_refs": selected,
        "need_extract_refs": selected,
        "answerable_from_summary_refs": [],
    }


def _normalize_ref_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value:
        ref = str(item or "").strip().upper()
        if re.fullmatch(r"W\d+", ref):
            normalized.append(ref)
        elif str(item).isdigit():
            normalized.append(f"W{int(item)}")
    return _unique(normalized)[:limit]


def _apply_weak_source_policy(
    query: str,
    selected: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    research_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    if _weak_sources_can_answer(query, research_plan):
        return selected
    stronger_selected = [item for item in selected if not _is_light_weak_source(item)]
    if stronger_selected:
        return stronger_selected
    stronger_candidates = [item for item in candidates if not _is_light_weak_source(item)]
    return stronger_candidates[:_light_extract_limit(research_plan)]


def _summary_fallback_candidates(
    query: str,
    candidates: list[dict[str, Any]],
    research_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    limit = min(3, len(candidates))
    if not candidates:
        return []
    stronger = [item for item in candidates if not _is_light_weak_source(item)]
    if stronger:
        return stronger[:limit]
    if _weak_sources_can_answer(query, research_plan):
        return candidates[:limit]
    return []


def _weak_sources_can_answer(query: str, research_plan: dict[str, Any]) -> bool:
    if research_plan.get("risk_level") == "high" or research_plan.get("freshness_sensitive"):
        return False
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    if not contract.get("allow_summary_only", True):
        return False
    text = f"{query} {research_plan.get('intent_label') or ''}"
    return bool(re.search(r"什么是|是什么|什么意思|关系|区别|差异|介绍|解释|who\s+is|what\s+is|relationship|difference|explain", text, re.I))


def _is_light_weak_source(source: dict[str, Any]) -> bool:
    tags = set(str(item) for item in source.get("quality_tags") or [])
    return bool(tags & {"weak_secondary_source", "low_quality_domain", "generic_homepage"})


def _llm_selected_refs(
    query: str,
    candidates: list[dict[str, Any]],
    research_plan: dict[str, Any],
    read_limit: int,
) -> list[str] | dict[str, list[str]]:
    try:
        config = load_config(str(Path(__file__).parent.parent.parent / "config.yaml"))
        if not config.llm_api_key:
            return []
        prompt = _build_candidate_selection_prompt(query, candidates, research_plan, read_limit)
        raw = _call_llm(
            api_key=config.llm_api_key,
            model=config.llm_model,
            base_url=config.llm_base_url or "https://api.deepseek.com",
            prompt=prompt,
            json_mode=True,
            max_tokens=1000,
            timeout_seconds=18,
            reserve_seconds=18,
        )
        payload = _json_payload(raw)
    except Exception:
        return []
    selected = _normalize_ref_list(payload.get("selected_refs") or payload.get("selected") or [], read_limit)
    need_extract = _normalize_ref_list(payload.get("need_extract_refs") or [], read_limit)
    summary_only = _normalize_ref_list(payload.get("answerable_from_summary_refs") or [], read_limit)
    if need_extract or summary_only:
        return {
            "selected_refs": selected or _unique([*need_extract, *summary_only])[:read_limit],
            "need_extract_refs": need_extract,
            "answerable_from_summary_refs": summary_only,
        }
    return selected


def _build_candidate_selection_prompt(
    query: str,
    candidates: list[dict[str, Any]],
    research_plan: dict[str, Any],
    read_limit: int,
) -> str:
    rows = []
    for source in candidates[:LIGHT_MAX_CANDIDATES]:
        rows.append(
            {
                "ref": source.get("ref"),
                # Search titles/descriptions are untrusted page metadata.
                "title": sanitize_untrusted_text(source.get("title") or ""),
                "url": source.get("url") or "",
                "domain": source.get("domain") or _domain(source.get("url") or ""),
                "description": sanitize_untrusted_text(
                    _clip_text(str(source.get("description") or source.get("excerpt") or ""), 360)
                ),
            }
        )
    return "\n".join(
        [
            "你负责为轻量联网答疑选择候选来源，并判断哪些需要阅读全文、哪些摘要已足够。",
            "只在给定候选里选择，不要新增 URL。",
            f"用户问题：{query}",
            f"最多选择 {read_limit} 个，覆盖面优先于重复来源。",
            "弱来源（博客、知乎、CSDN、公众号、SEO 聚合站）默认只作为线索；优先选择官方、技术报告、论文、GitHub、主流媒体或行业媒体。",
            "只有低风险概念解释、摘要已经能直接回答时，弱来源才可放入 answerable_from_summary_refs；不要为了弱来源本身要求全文读取。",
            f"问题属性：{json.dumps({'intent': research_plan.get('intent_label'), 'subject': research_plan.get('subject') or {}, 'answer_policy': research_plan.get('answer_policy') or {}}, ensure_ascii=False)}",
            "候选：",
            json.dumps(rows, ensure_ascii=False),
            '只返回 JSON：{"selected_refs":["W1","W3"],"need_extract_refs":["W1"],"answerable_from_summary_refs":["W3"],"reason":"简短理由"}',
        ]
    )


def _normalized_extract_map(extracted: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for url, content in extracted.items():
        clean = _clean_web_url(url)
        text = re.sub(r"\s+", " ", str(content or "")).strip()
        if clean and text:
            normalized[clean] = text
    return normalized


def _extract_failure_urls(raw_extract: str | dict[str, Any]) -> list[str]:
    try:
        payload = _json_payload(raw_extract)
    except Exception:
        return []
    failures: list[str] = []
    candidates = payload.get("results")
    if candidates is None and isinstance(payload.get("data"), dict):
        candidates = payload["data"].get("web") or payload["data"].get("results") or payload["data"].get("pages")
    if not isinstance(candidates, list):
        return []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = _clean_web_url(str(item.get("url") or item.get("source_url") or ""))
        if url and item.get("error"):
            failures.append(url)
    return failures


def _build_light_sources(
    selected: list[dict[str, Any]],
    extracted_by_url: dict[str, str],
    extract_failures: list[str],
) -> list[dict[str, Any]]:
    failures = set(extract_failures)
    sources: list[dict[str, Any]] = []
    for index, item in enumerate(selected, 1):
        url = _clean_web_url(str(item.get("url") or ""))
        if not url:
            continue
        extracted = extracted_by_url.get(url) or extracted_by_url.get(_clean_web_url(url))
        description = str(item.get("description") or item.get("excerpt") or "").strip()
        text = extracted or description
        if not text:
            continue
        extract_status = "full" if extracted else "summary"
        reason = "dashscope/local extracted page text" if extracted else "search summary fallback"
        source = {
            **item,
            "ref": f"W{index}",
            "id": url,
            "url": url,
            "domain": item.get("domain") or _domain(url),
            "excerpt": _clip_text(text, 2400),
            "description": description,
            "extract_status": extract_status,
            "extract_error": "extract failed" if url in failures and not extracted else "",
            "source_role": "web_material",
            "evidence_role": "light_web_material",
            "quality_reason": reason,
            "allowed_usage": ["background", "fact"],
            "confidence": "medium",
            "supports_claims": ["answer_user_question"],
            "claim_support": [
                {
                    "claim_id": "answer_user_question",
                    "support": "yes",
                    "reason": reason,
                }
            ],
        }
        sources.append(source)
    return sources[:LIGHT_SYNTHESIS_SOURCE_LIMIT]


def _light_search_quality(candidates: list[dict[str, Any]], research_plan: dict[str, Any]) -> dict[str, Any]:
    domains = _independent_source_count(candidates)
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    preferences = _sanitize_source_preferences(research_plan.get("source_preferences") or {})
    return {
        "passed": bool(candidates),
        "score": float(len(candidates)),
        "reason": "passed" if candidates else "no_results",
        "matched_terms": [],
        "matched_subject_terms": [],
        "core_terms": [],
        "required_terms": [],
        "trusted_sources": sum(1 for item in candidates if item.get("trusted_source")),
        "preferred_sources": sum(1 for item in candidates if item.get("domain") in set(preferences.get("preferred_domains") or [])),
        "top_result_hints": {},
        "evidence_status": "sufficient" if candidates else "insufficient",
        "rewrite_attempted": False,
        "rewritten_queries": [],
        "retrieval_health": {
            "candidate_count": len(candidates),
            "independent_domain_count": domains,
            "minimum_candidate_count": min(8, LIGHT_SIMPLE_CANDIDATES),
            "minimum_independent_domain_count": min(int(contract.get("minimum_independent_sources") or 1), 3),
            "needs_recall_repair": False,
            "repair_reason": "",
        },
    }


def _emit_from_thread(event_callback: ResearchEventCallback | None, event: str, **kwargs: Any) -> None:
    if event_callback is None:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _run_coro_sync(_emit_research_event_async(event_callback, event, **kwargs))
        return
    # Synchronous callers inside a running loop should not block on telemetry.


def _run_coro_sync(coro):
    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    import threading

    thread = threading.Thread(target=_runner, name="light-web-qa-event", daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")
