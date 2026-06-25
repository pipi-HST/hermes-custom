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
from pathlib import Path
from typing import Any

from agents.capture_agent import load_config

from bridge.research.adapters import _default_search_backend
from bridge.research.results import research_provider_status, web_research_result
from bridge.research.search import _independent_source_count
from bridge.research.search_infra import (
    collect_web_search_results,
    collect_web_search_results_async,
    prefilter_web_sources,
)
from bridge.research.synthesis import _call_llm, _trace_sources
from bridge.research.timing import _add_stage_timing, _empty_stage_timings
from bridge.research.types import (
    MAX_WEB_QUERIES,
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
LIGHT_RESULTS_PER_QUERY = 8
LIGHT_SYNTHESIS_SOURCE_LIMIT = 8


def run_light_web_qa(
    query: str,
    warnings: list[str],
    *,
    research_plan: dict[str, Any],
    search_adapter: ResearchSearchAdapter,
    extract_adapter: ResearchExtractAdapter,
) -> dict[str, Any]:
    """Run the synchronous lightweight web QA retrieval path."""
    started = time.monotonic()
    warning_start = len(warnings)
    stage_timings = _empty_stage_timings()
    queries = _light_queries(query, research_plan)
    search_started = time.monotonic()
    results = collect_web_search_results(
        queries,
        search_adapter=_limit_wrapped_search_adapter(search_adapter),
        warnings=warnings,
        backend_label=_default_search_backend(),
        max_queries=min(len(queries), MAX_WEB_QUERIES),
    )
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
    )


async def run_light_web_qa_async(
    query: str,
    warnings: list[str],
    *,
    research_plan: dict[str, Any],
    search_adapter: ResearchSearchAdapter,
    extract_adapter: ResearchExtractAdapter,
    event_callback: ResearchEventCallback | None = None,
) -> dict[str, Any]:
    """Run the async lightweight web QA retrieval path."""
    started = time.monotonic()
    warning_start = len(warnings)
    stage_timings = _empty_stage_timings()
    queries = _light_queries(query, research_plan)
    search_started = time.monotonic()
    results = await collect_web_search_results_async(
        queries,
        search_adapter=_limit_wrapped_search_adapter(search_adapter),
        warnings=warnings,
        event_callback=event_callback,
        backend_label=_default_search_backend(),
        max_queries=min(len(queries), MAX_WEB_QUERIES),
    )
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
        event_callback=event_callback,
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
    event_callback: ResearchEventCallback | None = None,
) -> dict[str, Any]:
    candidate_limit = _light_candidate_limit(research_plan)
    candidates = _light_candidates(query, raw_results, research_plan, limit=candidate_limit)
    quality = _light_search_quality(candidates, research_plan)
    await _emit_research_event_async(
        event_callback,
        "web_ranked",
        search_quality=quality,
        web_sources=_trace_sources(candidates),
    )
    selected = await asyncio.to_thread(_select_candidates, query, candidates, research_plan, warnings)
    selected = selected[:_light_extract_limit(research_plan)]
    skip_extract = _summary_only_is_enough(query, selected, research_plan)
    urls = [str(item.get("url") or "") for item in selected if item.get("url")]
    if skip_extract:
        urls = []
    extracted_by_url: dict[str, str] = {}
    extract_failures: list[str] = []
    if urls and _research_budget_available(3.0):
        await _emit_research_event_async(event_callback, "web_extract_start", urls=urls, web_sources=_trace_sources(selected))
        extract_started = time.monotonic()
        try:
            raw_extract = await asyncio.to_thread(extract_adapter, urls)
            if asyncio.iscoroutine(raw_extract):
                raw_extract = await raw_extract
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
        read_pages=len(urls),
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
    event_callback: ResearchEventCallback | None = None,
) -> dict[str, Any]:
    candidate_limit = _light_candidate_limit(research_plan)
    candidates = _light_candidates(query, raw_results, research_plan, limit=candidate_limit)
    quality = _light_search_quality(candidates, research_plan)
    _emit_from_thread(
        event_callback,
        "web_ranked",
        search_quality=quality,
        web_sources=_trace_sources(candidates),
    )
    selected = _select_candidates(query, candidates, research_plan, warnings)
    read_limit = _light_extract_limit(research_plan)
    selected = selected[:read_limit]
    skip_extract = _summary_only_is_enough(query, selected, research_plan)
    extracted_by_url: dict[str, str] = {}
    extract_failures: list[str] = []
    urls = [str(item.get("url") or "") for item in selected if item.get("url")]
    if skip_extract:
        urls = []
    if urls and _research_budget_available(3.0):
        _emit_from_thread(event_callback, "web_extract_start", urls=urls)
        extract_started = time.monotonic()
        try:
            raw_extract = extract_adapter(urls)
            if asyncio.iscoroutine(raw_extract):
                raw_extract = _run_coro_sync(raw_extract)
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
        read_pages=len(urls),
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
    read_pages: int,
) -> dict[str, Any]:
    if raw_results and not sources:
        warnings.append("联网搜索已有候选结果，但页面正文不可用；已保留搜索摘要作为有限材料。")
        sources = _build_light_sources(candidates[: min(3, len(candidates))], {}, [])
        quality["evidence_status"] = "limited"
        quality["reason"] = "summary_only_sources"
    provider_status = research_provider_status(
        started,
        warnings,
        warning_start,
        quality,
        [],
        sources=sources,
        stage_timings=stage_timings,
    )
    provider_status["path"] = "light_web_qa"
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


def _limit_wrapped_search_adapter(search_adapter: ResearchSearchAdapter) -> ResearchSearchAdapter:
    def _wrapped(search_query: str, limit: int) -> str | dict[str, Any]:
        return search_adapter(search_query, min(max(int(limit or LIGHT_RESULTS_PER_QUERY), 1), LIGHT_RESULTS_PER_QUERY))

    return _wrapped


def _light_candidate_limit(research_plan: dict[str, Any]) -> int:
    if research_plan.get("answer_depth") == "brief" and not research_plan.get("freshness_sensitive"):
        return LIGHT_SIMPLE_CANDIDATES
    return LIGHT_MAX_CANDIDATES


def _light_extract_limit(research_plan: dict[str, Any]) -> int:
    if research_plan.get("answer_depth") == "brief" and not research_plan.get("freshness_sensitive"):
        return 3
    if research_plan.get("answer_depth") == "detailed" or research_plan.get("freshness_sensitive") or research_plan.get("risk_level") == "high":
        return 6
    return 3


def _summary_only_is_enough(query: str, selected: list[dict[str, Any]], research_plan: dict[str, Any]) -> bool:
    if research_plan.get("risk_level") == "high" or research_plan.get("freshness_sensitive"):
        return False
    if research_plan.get("answer_depth") == "detailed":
        return False
    text = f"{query} {research_plan.get('intent_label') or ''}"
    if not re.search(r"什么是|是什么|什么意思|关系|区别|差异|介绍|who\\s+is|what\\s+is|relationship|difference", text, re.I):
        return False
    rich_summaries = [
        str(item.get("description") or item.get("excerpt") or "").strip()
        for item in selected
        if len(str(item.get("description") or item.get("excerpt") or "").strip()) >= 60
    ]
    return len(rich_summaries) >= 2


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
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    read_limit = _light_extract_limit(research_plan)
    selected_refs = _llm_selected_refs(query, candidates, research_plan, read_limit)
    if selected_refs:
        by_ref = {str(item.get("ref") or "").upper(): item for item in candidates}
        selected = [by_ref[ref] for ref in selected_refs if ref in by_ref]
        if selected:
            return selected
    return candidates[:read_limit]


def _llm_selected_refs(
    query: str,
    candidates: list[dict[str, Any]],
    research_plan: dict[str, Any],
    read_limit: int,
) -> list[str]:
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
    refs = payload.get("selected_refs") or payload.get("selected") or []
    if not isinstance(refs, list):
        return []
    normalized = []
    for item in refs:
        ref = str(item or "").strip().upper()
        if re.fullmatch(r"W\d+", ref):
            normalized.append(ref)
        elif str(item).isdigit():
            normalized.append(f"W{int(item)}")
    return _unique(normalized)[:read_limit]


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
                "title": source.get("title") or "",
                "url": source.get("url") or "",
                "domain": source.get("domain") or _domain(source.get("url") or ""),
                "description": _clip_text(str(source.get("description") or source.get("excerpt") or ""), 360),
            }
        )
    return "\n".join(
        [
            "你负责为轻量联网答疑选择值得阅读全文的网页。",
            "只在给定候选里选择，不要新增 URL。",
            f"用户问题：{query}",
            f"最多选择 {read_limit} 个，覆盖面优先于重复来源。",
            f"问题属性：{json.dumps({'intent': research_plan.get('intent_label'), 'subject': research_plan.get('subject') or {}, 'answer_policy': research_plan.get('answer_policy') or {}}, ensure_ascii=False)}",
            "候选：",
            json.dumps(rows, ensure_ascii=False),
            '只返回 JSON：{"selected_refs":["W1","W3"],"reason":"简短理由"}',
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
