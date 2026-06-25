"""Default web context answer path for knowledge workbench questions.

This module treats web search as external context for understanding and
knowledge processing.  It deliberately avoids the legacy evidence critic,
adoption/rejection taxonomy, and repair-search loops.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Awaitable

from agents.capture_agent import load_config

from bridge.result_envelope import success_envelope
from bridge.research.adapters import _default_search_backend
from bridge.research.synthesis import _call_llm
from bridge.research.timing import _add_stage_timing
from bridge.research.types import (
    ResearchEventCallback,
    ResearchExtractAdapter,
    ResearchSearchAdapter,
    ResearchSynthesizeAdapter,
)
from bridge.research.utils import (
    _clean_web_url,
    _clip_text,
    _domain,
    _json_payload,
    _parse_web_extract,
    _parse_web_search,
    _unique,
)

WEB_CONTEXT_MAX_QUERIES = 3
WEB_CONTEXT_RESULTS_PER_QUERY = 8
WEB_CONTEXT_MAX_MATERIALS = 6
WEB_CONTEXT_MAX_OPEN_PAGES = 2
WEB_CONTEXT_QUERY_TIMEOUT_SECONDS = 8
WEB_CONTEXT_SELECTION_TIMEOUT_SECONDS = 8
WEB_CONTEXT_SYNTHESIS_TIMEOUT_SECONDS = 35

LocalSearchAdapter = Callable[[str, list[str]], tuple[str, list[dict[str, Any]]]]


def web_context_answer(
    *,
    query: str,
    mode: str = "local_web",
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    search_adapter: ResearchSearchAdapter,
    extract_adapter: ResearchExtractAdapter,
    local_search_adapter: LocalSearchAdapter | None = None,
    synthesize_adapter: ResearchSynthesizeAdapter | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    clean_query = (query or "").strip()
    normalized_mode = _normalize_mode(mode)
    if not clean_query:
        return success_envelope(
            agent="research",
            intent="web_context_answer",
            chat_preview="请先输入要处理的问题。",
            meta={"query": query, "mode": normalized_mode, "path": "web_context"},
        )

    article_context = article_context or {}
    selection_context = selection_context or {}
    warnings: list[str] = []
    stage_timings = _empty_web_context_stage_timings()

    use_local = normalized_mode in {"local_only", "local_web"}
    use_web = normalized_mode in {"web_only", "local_web"}
    local_answer = ""
    knowledge_sources: list[dict[str, Any]] = []
    if use_local and local_search_adapter is not None:
        local_answer, knowledge_sources = local_search_adapter(clean_query, warnings)

    web_queries: list[str] = []
    candidates: list[dict[str, Any]] = []
    web_sources: list[dict[str, Any]] = []
    opened_urls: list[str] = []
    material_plan: dict[str, Any] = {"selected_refs": [], "open_refs": [], "answer_shape": ""}
    if use_web:
        search_started = time.monotonic()
        web_queries = _plan_queries(clean_query, article_context=article_context, selection_context=selection_context)
        candidates = _search_candidates(web_queries, search_adapter, warnings)
        _add_stage_timing(stage_timings, "search_ms", search_started)

        material_plan = _select_materials(clean_query, candidates, local_answer=local_answer)
        selected = _sources_by_refs(candidates, material_plan.get("selected_refs") or [])
        if not selected:
            selected = candidates[:WEB_CONTEXT_MAX_MATERIALS]

        open_refs = [str(ref).upper() for ref in material_plan.get("open_refs") or []]
        opened_urls = [str(item.get("url") or "") for item in selected if str(item.get("ref") or "").upper() in open_refs]
        opened_urls = [url for url in _unique(opened_urls) if url][:WEB_CONTEXT_MAX_OPEN_PAGES]
        extracted_by_url: dict[str, str] = {}
        extract_failures: list[str] = []
        if opened_urls:
            extract_started = time.monotonic()
            try:
                raw_extract = extract_adapter(opened_urls)
                if asyncio.iscoroutine(raw_extract):
                    raw_extract = _run_coro_sync(raw_extract)
                extracted_by_url = _normalized_extract_map(_parse_web_extract(raw_extract))
                extract_failures = _extract_failure_urls(raw_extract)
            except Exception as exc:
                warnings.append(f"网页打开失败，已使用搜索摘要：{exc}")
                extract_failures = opened_urls
            finally:
                _add_stage_timing(stage_timings, "extract_ms", extract_started)
        web_sources = _material_sources(selected, extracted_by_url, extract_failures)

    synthesis_input = _synthesis_payload(
        query=clean_query,
        mode=normalized_mode,
        article_context=article_context,
        selection_context=selection_context,
        local_answer=local_answer,
        knowledge_sources=knowledge_sources,
        web_sources=web_sources,
        web_queries=web_queries,
        material_plan=material_plan,
    )
    synthesis_started = time.monotonic()
    answer = (
        synthesize_adapter(synthesis_input)
        if synthesize_adapter
        else _generate_answer(synthesis_input, warnings)
    )
    _add_stage_timing(stage_timings, "synthesis_ms", synthesis_started)
    answer = _repair_answer_refs(answer, knowledge_sources, web_sources)
    cited_knowledge, cited_web = _cited_sources(answer, knowledge_sources, web_sources)
    if cited_knowledge or cited_web:
        knowledge_sources = cited_knowledge
        web_sources = cited_web

    provider_status = _provider_status(
        started,
        warnings=warnings,
        candidates=candidates,
        web_sources=web_sources,
        opened_urls=opened_urls,
        stage_timings=stage_timings,
    )
    research_trace = {
        "path": "web_context",
        "legacy_research": False,
        "web_queries": web_queries,
        "candidate_sources": _trace_context_sources(candidates),
        "material_sources": _trace_context_sources(web_sources),
        "candidate_count": len(candidates),
        "material_count": len(web_sources),
        "opened_pages": len(opened_urls),
        "source_counts": {
            "candidate": len(candidates),
            "material": len(web_sources),
            "web": len(web_sources),
            "knowledge": len(knowledge_sources),
        },
        "stage_timings": stage_timings,
        "warnings": list(warnings),
    }
    source_map = [
        *_source_map_items(knowledge_sources, source_type="knowledge"),
        *_source_map_items(web_sources, source_type="web"),
    ]
    return success_envelope(
        agent="research",
        intent="web_context_answer",
        chat_preview=answer,
        source_map=source_map,
        warnings=[],
        meta={
            "query": clean_query,
            "mode": normalized_mode,
            "path": "web_context",
            "legacy_research": False,
            "used_local": bool(knowledge_sources or local_answer),
            "used_web": bool(web_sources),
            "web_search_attempted": use_web,
            "knowledge_sources": knowledge_sources,
            "web_sources": web_sources,
            "web_queries": web_queries,
            "search_quality": {
                "passed": bool(web_sources or not use_web),
                "reason": "materials_available" if web_sources else ("not_requested" if not use_web else "no_web_materials"),
            },
            "rewritten_web_queries": [],
            "provider_status": provider_status,
            "research_trace": research_trace,
            "research_plan": {
                "path": "web_context",
                "intent_label": "web context answer",
                "needs_web": use_web,
                "mode": normalized_mode,
                "queries": web_queries,
            },
            "plan_status": "web_context",
        },
    )


async def async_web_context_answer(
    *,
    query: str,
    mode: str = "local_web",
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    search_adapter: ResearchSearchAdapter,
    extract_adapter: ResearchExtractAdapter,
    local_search_adapter: LocalSearchAdapter | None = None,
    synthesize_adapter: ResearchSynthesizeAdapter | None = None,
    event_callback: ResearchEventCallback | None = None,
) -> dict[str, Any]:
    clean_query = (query or "").strip()
    await _emit(event_callback, "plan", path="web_context", mode=_normalize_mode(mode), needs_web=mode in {"web_only", "local_web"})
    await _emit(event_callback, "web_search_start", path="web_context")
    result = await asyncio.to_thread(
        web_context_answer,
        query=clean_query,
        mode=mode,
        article_context=article_context,
        selection_context=selection_context,
        search_adapter=search_adapter,
        extract_adapter=extract_adapter,
        local_search_adapter=local_search_adapter,
        synthesize_adapter=synthesize_adapter,
    )
    meta = result.get("meta") or {}
    await _emit(
        event_callback,
        "web_ranked",
        path="web_context",
        search_quality=meta.get("search_quality") or {},
        web_sources=(meta.get("research_trace") or {}).get("candidate_sources") or [],
    )
    await _emit(event_callback, "synthesis_start", path="web_context")
    return result


def _normalize_mode(mode: str) -> str:
    return mode if mode in {"current_article_only", "local_only", "local_web", "web_only"} else "local_web"


def _plan_queries(
    query: str,
    *,
    article_context: dict[str, Any],
    selection_context: dict[str, Any],
) -> list[str]:
    prompt = "\n".join(
        [
            "你要为知识工作台的问题生成网页搜索词。",
            "目标是帮助用户理解、补充背景、扩展材料，不是做证据审判。",
            "只返回 JSON：{\"queries\":[\"...\"]}，最多 3 个。",
            f"用户问题：{query}",
            f"选区：{_clip_text(str(selection_context.get('selected_text') or ''), 240)}",
            f"文章标题：{_clip_text(str(article_context.get('title') or ''), 120)}",
        ]
    )
    payload = _llm_json(prompt, timeout_seconds=WEB_CONTEXT_QUERY_TIMEOUT_SECONDS)
    queries = [str(item or "").strip() for item in payload.get("queries") or []]
    queries = [item for item in queries if item]
    return _unique([*queries, query])[:WEB_CONTEXT_MAX_QUERIES] or [query]


def _search_candidates(
    queries: list[str],
    search_adapter: ResearchSearchAdapter,
    warnings: list[str],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for search_query in queries[:WEB_CONTEXT_MAX_QUERIES]:
        try:
            parsed = _parse_web_search(search_adapter(search_query, WEB_CONTEXT_RESULTS_PER_QUERY))
        except Exception as exc:
            warnings.append(f"网页搜索失败，已使用可用材料：{exc}")
            continue
        for item in parsed:
            url = _clean_web_url(str(item.get("url") or ""))
            key = (url or str(item.get("title") or "")).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "ref": f"W{len(candidates) + 1}",
                    "type": "web",
                    "id": url or f"web-{len(candidates) + 1}",
                    "title": str(item.get("title") or url or f"网页来源 {len(candidates) + 1}"),
                    "url": url,
                    "domain": _domain(url),
                    "description": str(item.get("description") or item.get("excerpt") or "").strip(),
                    "excerpt": str(item.get("description") or item.get("excerpt") or "").strip(),
                    "query": search_query,
                    "source_role": "context_material",
                    "extract_status": "summary",
                }
            )
    return candidates


def _select_materials(query: str, candidates: list[dict[str, Any]], *, local_answer: str) -> dict[str, Any]:
    if not candidates:
        return {"selected_refs": [], "open_refs": [], "answer_shape": ""}
    rows = [
        {
            "ref": item.get("ref"),
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "snippet": _clip_text(str(item.get("description") or ""), 300),
        }
        for item in candidates[:12]
    ]
    prompt = "\n".join(
        [
            "你在知识工作台里选择网页材料。",
            "请判断哪些搜索结果有助于回答用户问题，哪些需要打开网页看更多内容。",
            "不要做可靠性审判；博客、媒体、论坛都可以作为理解材料，但强结论需要在回答里降低语气。",
            "只返回 JSON：{\"selected_refs\":[\"W1\"],\"open_refs\":[\"W2\"],\"answer_shape\":\"简短说明\"}",
            "selected_refs 最多 6 个；open_refs 最多 2 个；如果摘要足够，open_refs 返回空数组。",
            f"用户问题：{query}",
            f"本地材料摘要：{_clip_text(local_answer, 300)}",
            "搜索结果：",
            json.dumps(rows, ensure_ascii=False),
        ]
    )
    payload = _llm_json(prompt, timeout_seconds=WEB_CONTEXT_SELECTION_TIMEOUT_SECONDS)
    selected = _valid_refs(payload.get("selected_refs"), candidates, WEB_CONTEXT_MAX_MATERIALS)
    open_refs = _valid_refs(payload.get("open_refs"), candidates, WEB_CONTEXT_MAX_OPEN_PAGES)
    if not selected:
        selected = [str(item.get("ref")) for item in candidates[:WEB_CONTEXT_MAX_MATERIALS]]
    return {
        "selected_refs": selected,
        "open_refs": [ref for ref in open_refs if ref in set(selected)],
        "answer_shape": str(payload.get("answer_shape") or ""),
    }


def _valid_refs(value: Any, candidates: list[dict[str, Any]], limit: int) -> list[str]:
    available = {str(item.get("ref") or "").upper() for item in candidates}
    refs: list[str] = []
    if isinstance(value, list):
        for item in value:
            ref = str(item or "").strip().upper()
            if ref in available and ref not in refs:
                refs.append(ref)
            if len(refs) >= limit:
                break
    return refs


def _sources_by_refs(candidates: list[dict[str, Any]], refs: list[str]) -> list[dict[str, Any]]:
    wanted = [str(ref).upper() for ref in refs]
    by_ref = {str(item.get("ref") or "").upper(): item for item in candidates}
    return [by_ref[ref] for ref in wanted if ref in by_ref]


def _material_sources(
    selected: list[dict[str, Any]],
    extracted_by_url: dict[str, str],
    extract_failures: list[str],
) -> list[dict[str, Any]]:
    failures = set(extract_failures)
    sources: list[dict[str, Any]] = []
    for index, item in enumerate(selected, 1):
        url = _clean_web_url(str(item.get("url") or ""))
        text = extracted_by_url.get(url) or str(item.get("description") or item.get("excerpt") or "").strip()
        if not text:
            continue
        sources.append(
            {
                **item,
                "ref": f"W{index}",
                "id": url or str(item.get("id") or f"web-{index}"),
                "url": url,
                "domain": item.get("domain") or _domain(url),
                "excerpt": _clip_text(text, 2400),
                "description": str(item.get("description") or "").strip(),
                "extract_status": "full" if url in extracted_by_url else "summary",
                "extract_error": "extract failed" if url in failures and url not in extracted_by_url else "",
                "source_role": "context_material",
            }
        )
    return sources


def _synthesis_payload(
    *,
    query: str,
    mode: str,
    article_context: dict[str, Any],
    selection_context: dict[str, Any],
    local_answer: str,
    knowledge_sources: list[dict[str, Any]],
    web_sources: list[dict[str, Any]],
    web_queries: list[str],
    material_plan: dict[str, Any],
) -> dict[str, Any]:
    return {
        "query": query,
        "mode": mode,
        "article_context": article_context,
        "selection_context": selection_context,
        "local_answer": local_answer,
        "knowledge_sources": knowledge_sources,
        "web_sources": web_sources,
        "web_queries": web_queries,
        "material_plan": material_plan,
        "used_local": bool(local_answer or knowledge_sources),
        "used_web": bool(web_sources),
    }


def _generate_answer(payload: dict[str, Any], warnings: list[str]) -> str:
    source_rows = [
        {
            "ref": source.get("ref"),
            "title": source.get("title"),
            "url": source.get("url"),
            "excerpt": _clip_text(str(source.get("excerpt") or ""), 900),
        }
        for source in payload.get("web_sources") or []
    ]
    knowledge_rows = [
        {
            "ref": source.get("ref"),
            "title": source.get("title"),
            "excerpt": _clip_text(str(source.get("excerpt") or source.get("content") or ""), 600),
        }
        for source in payload.get("knowledge_sources") or []
    ]
    prompt = "\n".join(
        [
            "你是知识工作台里的联网辅助理解模块。",
            "直接回答用户问题，优先帮助用户理解、整理、比较、加工信息。",
            "不要写搜索过程、系统反思、免责声明；必要的不确定性要写成答案边界。",
            "可以使用博客、媒体、论坛作为观点或解释材料，但不要把它们包装成官方事实。",
            "引用只能使用给定编号，如 [W1] 或 [K1]。",
            f"用户问题：{payload['query']}",
            f"当前选区：{_clip_text(str((payload.get('selection_context') or {}).get('selected_text') or ''), 600)}",
            f"本地材料：{_clip_text(str(payload.get('local_answer') or ''), 1000)}",
            f"本地来源：{json.dumps(knowledge_rows, ensure_ascii=False)}",
            f"网页材料：{json.dumps(source_rows, ensure_ascii=False)}",
        ]
    )
    try:
        return _call_text_llm(prompt, timeout_seconds=WEB_CONTEXT_SYNTHESIS_TIMEOUT_SECONDS)
    except Exception as exc:
        warnings.append(f"回答生成失败，已使用材料摘要：{exc}")
        return _fallback_answer(payload)


def _fallback_answer(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    local_answer = str(payload.get("local_answer") or "").strip()
    if local_answer:
        lines.append(_clip_text(local_answer, 500))
    for source in (payload.get("web_sources") or [])[:4]:
        text = _clip_text(str(source.get("excerpt") or source.get("description") or ""), 240)
        if text:
            lines.append(f"- {text} [{source.get('ref')}]")
    return "\n".join(lines).strip() or "暂时没有足够材料回答这个问题。"


def _llm_json(prompt: str, *, timeout_seconds: float) -> dict[str, Any]:
    try:
        raw = _call_text_llm(prompt, timeout_seconds=timeout_seconds, json_mode=True, max_tokens=800)
        return _json_payload(raw)
    except Exception:
        return {}


def _call_text_llm(
    prompt: str,
    *,
    timeout_seconds: float,
    json_mode: bool = False,
    max_tokens: int = 1600,
) -> str:
    config = load_config(str(Path(__file__).resolve().parent.parent / "config.yaml"))
    if not config.llm_api_key:
        raise RuntimeError("LLM API key is not configured")
    return _call_llm(
        api_key=config.llm_api_key,
        model=config.llm_model,
        base_url=config.llm_base_url or "https://api.deepseek.com",
        prompt=prompt,
        json_mode=json_mode,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        reserve_seconds=0,
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
    results = payload.get("results")
    if results is None and isinstance(payload.get("data"), dict):
        results = payload["data"].get("web") or payload["data"].get("results") or payload["data"].get("pages")
    if not isinstance(results, list):
        return []
    failures: list[str] = []
    for item in results:
        if isinstance(item, dict) and item.get("error"):
            url = _clean_web_url(str(item.get("url") or item.get("source_url") or ""))
            if url:
                failures.append(url)
    return failures


def _repair_answer_refs(answer: str, knowledge_sources: list[dict[str, Any]], web_sources: list[dict[str, Any]]) -> str:
    available = {str(source.get("ref") or "").upper() for source in [*knowledge_sources, *web_sources]}

    def replace(match: re.Match[str]) -> str:
        ref = match.group(1).upper()
        return f"[{ref}]" if ref in available else ""

    return re.sub(r"\[([WK]\d+)\]", replace, answer or "").strip()


def _cited_sources(
    answer: str,
    knowledge_sources: list[dict[str, Any]],
    web_sources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    refs = {match.upper() for match in re.findall(r"\[([WK]\d+)\]", answer or "")}
    if not refs:
        return knowledge_sources, web_sources
    return (
        [source for source in knowledge_sources if str(source.get("ref") or "").upper() in refs],
        [source for source in web_sources if str(source.get("ref") or "").upper() in refs],
    )


def _source_map_items(sources: list[dict[str, Any]], *, source_type: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in sources:
        ref = str(source.get("ref") or "").strip()
        output.append(
            {
                "ref": ref,
                "role": "context",
                "type": source_type,
                "id": str(source.get("id") or source.get("url") or ref),
                "title": str(source.get("title") or ref),
                "url": str(source.get("url") or ""),
                "status": source.get("extract_status") or "",
            }
        )
    return output


def _trace_context_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    traced: list[dict[str, Any]] = []
    for source in sources[:12]:
        traced.append(
            {
                "ref": str(source.get("ref") or ""),
                "title": str(source.get("title") or ""),
                "url": str(source.get("url") or ""),
                "domain": str(source.get("domain") or ""),
                "status": str(source.get("extract_status") or ""),
                "role": str(source.get("source_role") or "context_material"),
            }
        )
    return traced


def _provider_status(
    started: float,
    *,
    warnings: list[str],
    candidates: list[dict[str, Any]],
    web_sources: list[dict[str, Any]],
    opened_urls: list[str],
    stage_timings: dict[str, Any],
) -> dict[str, Any]:
    return {
        "path": "web_context",
        "search_backend": _default_search_backend(),
        "extract_backend": "",
        "search_elapsed_ms": int((time.monotonic() - started) * 1000),
        "candidate_count": len(candidates),
        "used_sources": len(web_sources),
        "opened_pages": len(opened_urls),
        "extracted_pages": sum(1 for source in web_sources if source.get("extract_status") == "full"),
        "summary_only_pages": sum(1 for source in web_sources if source.get("extract_status") == "summary"),
        "fallback_attempts": [],
        "warnings": list(warnings),
        "stage_timings": stage_timings,
    }


def _empty_web_context_stage_timings() -> dict[str, int]:
    return {
        "search_ms": 0,
        "extract_ms": 0,
        "synthesis_ms": 0,
    }


def _run_coro_sync(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)


async def _emit(event_callback: ResearchEventCallback | None, event: str, **kwargs: Any) -> None:
    if event_callback is None:
        return
    result = event_callback({"type": event, **kwargs})
    if asyncio.iscoroutine(result):
        await result
