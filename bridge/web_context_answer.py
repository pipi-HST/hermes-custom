"""Default web context answer path for knowledge workbench questions.

This module treats web search as external context for understanding and
knowledge processing.  It deliberately avoids the legacy evidence critic,
adoption/rejection taxonomy, and repair-search loops.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any, Callable, Awaitable

import httpx

from agents.capture_agent import load_config

from bridge.result_envelope import success_envelope
from bridge.research.adapters import _default_search_backend
from bridge.research.planner import (
    _max_initial_search_queries_for_plan,
    plan_research,
)
from bridge.research.synthesis import _call_llm
from bridge.research.types import (
    MAX_WEB_QUERIES,
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

WEB_CONTEXT_RESULTS_PER_QUERY = 8
WEB_CONTEXT_MAX_MATERIALS = 6
WEB_CONTEXT_MAX_OPEN_PAGES = 2
WEB_CONTEXT_FULL_ARTICLE_CHARS = 4000
WEB_CONTEXT_SUMMARY_CHARS = 800
WEB_CONTEXT_SURROUNDING_CHARS = 500
DEFAULT_WEB_CONTEXT_SEARCH_QUERY_TIMEOUT_SECONDS = 18.0
WEB_CONTEXT_SELECTION_TIMEOUT_SECONDS = 8
WEB_CONTEXT_SYNTHESIS_TIMEOUT_SECONDS = 35
WEB_CONTEXT_SUMMARY_TIMEOUT_SECONDS = 30
WEB_CONTEXT_HISTORY_SUMMARY_CHARS = 1000
WEB_CONTEXT_HISTORY_MESSAGES = 12
WEB_CONTEXT_HISTORY_MESSAGE_CHARS = 1200
WEB_CONTEXT_HISTORY_OVERFLOW_MESSAGES = 40

WEB_CONTEXT_ANSWER_STYLE_CONTRACT_LINES = (
    "回答应以解决用户问题为第一目标，而不是展示检索过程或追求形式整齐。",
    "你负责语义判断：自行判断用户真正想知道什么、材料分别适合支撑什么、答案应该采用什么结构。",
    "先直接回应用户真正问的问题，给出核心判断或核心解释。",
    "不要套固定题型模板；如果表格有助于比较、分类或辅助扫描，可以使用表格。",
    "表格只能辅助理解，不能替代回答；使用表格后，应补充必要解释、取舍建议或边界说明。",
    "不要为了形式整齐而省略关键解释；如果问题更适合段落、列表或定义，就不要强行表格。",
    "不要以“从搜索结果看”“根据检索结果”“需要先明确一点”等过程语开头。",
    "不要写系统反思或免责声明；必要的不确定性要写成答案内容的一部分。",
)
WEB_CONTEXT_ANSWER_STYLE_CONTRACT = "\n".join(WEB_CONTEXT_ANSWER_STYLE_CONTRACT_LINES)

LocalSearchAdapter = Callable[[str, list[str]], tuple[str, list[dict[str, Any]]]]
AnswerDeltaCallback = Callable[[str], None]
ProgressCallback = Callable[..., None]

PROCESS_LANGUAGE_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"从(?:搜索结果|联网搜索|检索结果|现有(?:资料|信息)|本次(?:搜索|检索))(?:(?:来看)|(?:看)|(?:显示)|(?:可见)|(?:可知))?[，,：:\\s]*"
    r"|根据(?:搜索结果|联网搜索|检索结果)[，,：:\\s]*"
    r"|需要先明确一点[：:\\s]*"
    r")",
    re.I,
)
PROCESS_LANGUAGE_LINE_RE = re.compile(
    r"^\s*(?:"
    r"(?:从搜索结果|从联网搜索|从检索结果|本次搜索|本次检索).{0,40}$"
    r"|(?:以下|下面)是(?:搜索|检索|整理)到的.{0,30}$"
    r")\s*$",
    re.I,
)

MAX_SELECTION_SEARCH_CHARS = 1000
ANSWER_REF_RE = re.compile(r"\\?\[([KW]\d+)\\?\]", re.I)


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
    answer_delta_callback: AnswerDeltaCallback | None = None,
    progress_callback: ProgressCallback | None = None,
    history: list[dict[str, str]] | None = None,
    history_summary: str = "",
    history_summary_version: int = 0,
    history_overflow: list[dict[str, str]] | None = None,
    force_web: bool = False,
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

    warnings: list[str] = []
    selection_context = selection_context or {}
    article_context = _normalized_article_context(article_context or {}, selection_context, warnings)
    history_messages = _normalized_history_messages(history)
    history_summary = _clip_text(str(history_summary or ""), WEB_CONTEXT_HISTORY_SUMMARY_CHARS)
    stage_timings = _empty_web_context_stage_timings()

    if normalized_mode == "current_article_only":
        research_plan = _direct_article_plan(normalized_mode)
    else:
        planning_started = time.monotonic()
        plan_kwargs: dict[str, Any] = {}
        if history_messages or history_summary:
            plan_kwargs["history"] = history_messages
            plan_kwargs["history_summary"] = history_summary
        research_plan = plan_research(
            clean_query,
            mode=normalized_mode,
            article_context=article_context,
            selection_context=selection_context,
            **plan_kwargs,
        )
        _add_web_context_stage_timing(stage_timings, "planning_ms", planning_started)
    use_local = _mode_needs_local(normalized_mode)
    use_web = _mode_needs_web(normalized_mode, research_plan)
    planned_queries = [str(item or "").strip() for item in research_plan.get("queries") or [] if str(item or "").strip()]
    if force_web and normalized_mode == "local_web":
        use_web = True
        if not planned_queries:
            planned_queries = [clean_query]
        research_plan = {
            **research_plan,
            "needs_web": True,
            "queries": planned_queries,
            "plan_status": f"{research_plan.get('plan_status') or 'planned'}_web_forced_by_request",
        }
    query_cap = _query_cap(normalized_mode, research_plan)
    web_queries = planned_queries[:query_cap]
    skipped_query_statuses = (
        _skipped_query_statuses(planned_queries, reason="web_not_requested")
        if normalized_mode == "local_web" and planned_queries and not use_web
        else []
    )
    _progress(
        progress_callback,
        "plan",
        mode=normalized_mode,
        plan_status=research_plan.get("plan_status") or ("planned_web" if use_web else "planned"),
        needs_web=use_web,
        needs_local=use_local,
        queries=web_queries or planned_queries,
        force_web=bool(force_web and normalized_mode == "local_web"),
        intent_label=research_plan.get("intent_label"),
        answer_depth=research_plan.get("answer_depth"),
    )
    local_answer = ""
    knowledge_sources: list[dict[str, Any]] = []
    if use_local and local_search_adapter is not None:
        local_started = time.monotonic()
        local_answer, knowledge_sources = local_search_adapter(
            _local_search_query(clean_query, selection_context, article_context),
            warnings,
        )
        knowledge_sources = _prioritize_article_sources(
            knowledge_sources,
            article_context=article_context,
            selection_context=selection_context,
        )
        _add_web_context_stage_timing(stage_timings, "local_search_ms", local_started)

    candidates: list[dict[str, Any]] = []
    query_statuses: list[dict[str, Any]] = list(skipped_query_statuses)
    web_sources: list[dict[str, Any]] = []
    opened_urls: list[str] = []
    material_plan: dict[str, Any] = {"selected_refs": [], "open_refs": [], "answer_shape": ""}
    if use_web:
        search_started = time.monotonic()
        if not web_queries:
            web_queries = [clean_query]
        _progress(progress_callback, "queries_ready", queries=web_queries, query_count=len(web_queries))
        candidates, executed_statuses = _search_candidates(web_queries, search_adapter, warnings, progress_callback=progress_callback)
        query_statuses.extend(executed_statuses)
        _add_web_context_stage_timing(stage_timings, "search_ms", search_started)

        material_plan = _select_materials(
            clean_query,
            candidates,
            local_answer=local_answer,
            article_context=article_context,
            selection_context=selection_context,
        )
        selected = _sources_by_refs(candidates, material_plan.get("selected_refs") or [])
        if not selected:
            selected = candidates[:WEB_CONTEXT_MAX_MATERIALS]

        open_refs = [str(ref).upper() for ref in material_plan.get("open_refs") or []]
        opened_urls = [str(item.get("url") or "") for item in selected if str(item.get("ref") or "").upper() in open_refs]
        opened_urls = [url for url in _unique(opened_urls) if url][:WEB_CONTEXT_MAX_OPEN_PAGES]
        _progress(
            progress_callback,
            "materials_selected",
            candidate_count=len(candidates),
            material_count=len(selected),
            opened_pages=len(opened_urls),
            web_sources=_trace_context_sources(selected),
        )
        extracted_by_url: dict[str, str] = {}
        extract_failures: list[str] = []
        if opened_urls:
            extract_started = time.monotonic()
            _progress(progress_callback, "web_extract_start", urls=opened_urls, opened_pages=len(opened_urls))
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
                _add_web_context_stage_timing(stage_timings, "extract_ms", extract_started)
        web_sources = _material_sources(selected, extracted_by_url, extract_failures)
        warnings.extend(
            _query_warning_messages(
                query_statuses,
                len(web_sources),
                has_existing_material=False
                if normalized_mode == "web_only"
                else _has_existing_answer_material(
                    local_answer=local_answer,
                    knowledge_sources=knowledge_sources,
                    article_context=article_context,
                    selection_context=selection_context,
                ),
            )
        )
        _progress(
            progress_callback,
            "web_extract_done",
            candidate_count=len(candidates),
            material_count=len(web_sources),
            opened_pages=len(opened_urls),
            extracted_pages=sum(1 for source in web_sources if source.get("extract_status") == "full"),
            summary_only_pages=sum(1 for source in web_sources if source.get("extract_status") == "summary"),
            web_sources=_trace_context_sources(web_sources),
        )

    synthesis_input = _synthesis_payload(
        query=clean_query,
        mode=normalized_mode,
        article_context=article_context,
        selection_context=selection_context,
        research_plan=research_plan,
        local_answer=local_answer,
        knowledge_sources=knowledge_sources,
        web_sources=web_sources,
        web_queries=web_queries,
        material_plan=material_plan,
        history=history_messages,
        history_summary=history_summary,
    )
    retrieved_knowledge_sources = list(knowledge_sources)
    material_web_sources = list(web_sources)
    synthesis_skipped = False
    reasoning_capture: dict[str, Any] = {}
    synthesis_started = time.monotonic()
    if use_web and not _has_synthesis_material(synthesis_input):
        warnings.append("没有可用材料，已跳过空材料回答生成。")
        synthesis_skipped = True
        answer = _fallback_answer(synthesis_input)
    else:
        answer = (
            synthesize_adapter(synthesis_input)
            if synthesize_adapter
            else _generate_answer(
                synthesis_input,
                warnings,
                answer_delta_callback=answer_delta_callback,
                reasoning_capture=reasoning_capture,
            )
        )
    _add_web_context_stage_timing(stage_timings, "synthesis_ms", synthesis_started)
    if normalized_mode == "current_article_only":
        answer = _strip_answer_refs(answer)
    answer = _normalize_answer_refs(answer)
    answer = _repair_answer_refs(answer, knowledge_sources, web_sources)
    answer = _remove_process_language(answer)
    cited_knowledge, cited_web = _cited_sources(answer, knowledge_sources, web_sources)
    if cited_knowledge:
        knowledge_sources = cited_knowledge
    if use_web:
        web_sources = _merge_sources_by_ref(cited_web, material_web_sources)
    elif cited_web:
        web_sources = cited_web

    provider_status = _provider_status(
        started,
        warnings=warnings,
        candidates=candidates,
        web_sources=web_sources,
        opened_urls=opened_urls,
        stage_timings=stage_timings,
        query_statuses=query_statuses,
        web_attempted=use_web,
        synthesis_skipped=synthesis_skipped,
    )
    total_elapsed_ms = int((time.monotonic() - started) * 1000)
    provider_status["total_elapsed_ms"] = total_elapsed_ms
    summary_update = _updated_history_summary(
        history_summary=history_summary,
        history_summary_version=history_summary_version,
        history_overflow=history_overflow,
        warnings=warnings,
    )
    provider_status["warnings"] = list(warnings)
    reasoning_trace = _reasoning_trace_from_capture(reasoning_capture)
    research_trace = {
        "path": "web_context",
        "legacy_research": False,
        "mode": normalized_mode,
        "intent_label": research_plan.get("intent_label"),
        "answer_depth": research_plan.get("answer_depth"),
        "plan_status": research_plan.get("plan_status"),
        "subject": research_plan.get("subject") or {},
        "source_preferences": research_plan.get("source_preferences") or {},
        "evidence_contract": research_plan.get("evidence_contract") or {},
        "answer_contract": research_plan.get("answer_contract") or {},
        "needs_web": use_web,
        "needs_local": use_local,
        "force_web": bool(force_web and normalized_mode == "local_web"),
        "queries": web_queries or planned_queries,
        "selected_text_present": bool(str(selection_context.get("selected_text") or "").strip()),
        "selected_text_chars": len(str(selection_context.get("selected_text") or "").strip()),
        "history_message_count": len(history_messages),
        "history_summary_version": _coerced_summary_version(history_summary_version),
        "article_context_source": article_context.get("context_source"),
        "context_warnings": article_context.get("context_warnings") or [],
        "used_article_context": bool(article_context.get("context_text") or article_context.get("body")),
        "used_local": bool(knowledge_sources or local_answer),
        "used_web": bool(web_sources),
        "web_search_attempted": use_web,
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
        "synthesis_skipped": synthesis_skipped,
        "query_statuses": query_statuses,
        "total_elapsed_ms": total_elapsed_ms,
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
        warnings=list(warnings),
        meta={
            "query": clean_query,
            "mode": normalized_mode,
            "path": "web_context",
            "legacy_research": False,
            "used_local": bool(knowledge_sources or local_answer),
            "used_web": bool(web_sources),
            "web_search_attempted": use_web,
            "knowledge_sources": knowledge_sources,
            "retrieved_knowledge_sources": retrieved_knowledge_sources,
            "web_sources": web_sources,
            "retrieved_web_sources": candidates,
            "web_queries": web_queries,
            "search_quality": {
                "passed": bool(web_sources or not use_web),
                "reason": "materials_available" if web_sources else ("not_requested" if not use_web else "no_web_materials"),
            },
            "rewritten_web_queries": [],
            "provider_status": provider_status,
            "research_trace": research_trace,
            "reasoning_trace": reasoning_trace,
            **summary_update,
            "research_plan": {
                **research_plan,
                "path": "web_context",
                "needs_web": use_web,
                "needs_local": use_local,
                "force_web": bool(force_web and normalized_mode == "local_web"),
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
    history: list[dict[str, str]] | None = None,
    history_summary: str = "",
    history_summary_version: int = 0,
    history_overflow: list[dict[str, str]] | None = None,
    force_web: bool = False,
) -> dict[str, Any]:
    clean_query = (query or "").strip()
    await _emit(event_callback, "planning_start", path="web_context", mode=_normalize_mode(mode))
    loop = asyncio.get_running_loop()
    streaming_started = False
    delta_buffer: list[str] = []
    last_delta_emit = time.monotonic()

    def emit_from_thread(event: str, **kwargs: Any) -> None:
        if event_callback is None:
            return
        future = asyncio.run_coroutine_threadsafe(
            _emit(event_callback, event, path="web_context", **kwargs),
            loop,
        )
        try:
            future.result(timeout=2.0)
        except Exception:
            return

    def answer_delta_callback(delta: str) -> None:
        nonlocal last_delta_emit, streaming_started
        if not delta:
            return
        if not streaming_started:
            streaming_started = True
            emit_from_thread("synthesis_start")
        delta_buffer.append(delta)
        now = time.monotonic()
        buffered = "".join(delta_buffer)
        if len(buffered) >= 96 or now - last_delta_emit >= 0.12:
            delta_buffer.clear()
            last_delta_emit = now
            emit_from_thread("answer_delta", delta=buffered)

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
        answer_delta_callback=None if synthesize_adapter else answer_delta_callback,
        progress_callback=emit_from_thread,
        history=history,
        history_summary=history_summary,
        history_summary_version=history_summary_version,
        history_overflow=history_overflow,
        force_web=force_web,
    )
    if delta_buffer:
        buffered = "".join(delta_buffer)
        delta_buffer.clear()
        last_delta_emit = time.monotonic()
        await _emit(event_callback, "answer_delta", path="web_context", delta=buffered)
    meta = result.get("meta") or {}
    await _emit(
        event_callback,
        "web_ranked",
        path="web_context",
        search_quality=meta.get("search_quality") or {},
        web_sources=(meta.get("research_trace") or {}).get("candidate_sources") or [],
    )
    trace = meta.get("research_trace") if isinstance(meta.get("research_trace"), dict) else {}
    provider_status = meta.get("provider_status") if isinstance(meta.get("provider_status"), dict) else {}
    synthesis_skipped = bool(trace.get("synthesis_skipped") or provider_status.get("synthesis_skipped"))
    if not streaming_started and not synthesis_skipped:
        await _emit(event_callback, "synthesis_start", path="web_context")
    if meta.get("updated_history_summary"):
        await _emit(
            event_callback,
            "summary_updated",
            path="web_context",
            updated_history_summary=meta.get("updated_history_summary"),
            updated_history_summary_version=meta.get("updated_history_summary_version"),
        )
    return result


def _normalize_mode(mode: str) -> str:
    return mode if mode in {"current_article_only", "local_only", "local_web", "web_only"} else "local_web"


def _normalized_article_context(
    article_context: dict[str, Any],
    selection_context: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    body = str(article_context.get("body") or article_context.get("content") or "")
    summary = _clip_text(str(article_context.get("summary") or ""), WEB_CONTEXT_SUMMARY_CHARS)
    heading_path = selection_context.get("heading_path") or []
    if not isinstance(heading_path, list):
        heading_path = []
    context_warnings: list[str] = []
    if len(body) <= WEB_CONTEXT_FULL_ARTICLE_CHARS:
        context_source = "full_article"
        context_text = body
    else:
        section = _section_for_heading_path(body, [str(item) for item in heading_path])
        if section:
            context_source = "current_section"
            context_text = section
            if len(context_text) > WEB_CONTEXT_FULL_ARTICLE_CHARS:
                context_text = _clip_text(context_text, WEB_CONTEXT_FULL_ARTICLE_CHARS)
                context_warnings.append("current_section_too_long_clipped")
        else:
            context_source = "surrounding_fallback"
            context_text = "\n".join(
                part
                for part in [
                    _clip_text(str(selection_context.get("surrounding_before") or ""), WEB_CONTEXT_SURROUNDING_CHARS),
                    str(selection_context.get("selected_text") or ""),
                    _clip_text(str(selection_context.get("surrounding_after") or ""), WEB_CONTEXT_SURROUNDING_CHARS),
                ]
                if part
            )
            context_warnings.append("section_resolution_failed")
    return {
        "title": str(article_context.get("title") or ""),
        "summary": summary,
        "body": body,
        "context_text": context_text,
        "context_source": context_source,
        "context_warnings": context_warnings,
        "folder_path": str(article_context.get("folder_path") or ""),
        "tags": article_context.get("tags") if isinstance(article_context.get("tags"), list) else [],
        "file_path": str(article_context.get("file_path") or ""),
    }


def _section_for_heading_path(body: str, heading_path: list[str]) -> str:
    wanted = [item.strip().lstrip("#").strip() for item in heading_path if item.strip()]
    if not body.strip() or not wanted:
        return ""
    headings: list[tuple[int, str, int, int]] = []
    for match in re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", body):
        headings.append((len(match.group(1)), match.group(2).strip(), match.start(), match.end()))
    if not headings:
        return ""
    target_index = -1
    target_title = wanted[-1]
    for index, (_level, title, _start, _end) in enumerate(headings):
        if title == target_title:
            target_index = index
    if target_index < 0:
        return ""
    level, _title, _start, content_start = headings[target_index]
    content_end = len(body)
    for next_level, _next_title, next_start, _next_end in headings[target_index + 1:]:
        if next_level <= level:
            content_end = next_start
            break
    return body[content_start:content_end].strip()


def _local_search_query(
    query: str,
    selection_context: dict[str, Any] | None,
    article_context: dict[str, Any] | None = None,
) -> str:
    selected_text = str((selection_context or {}).get("selected_text") or "").strip()
    article_context = article_context or {}
    title = str(article_context.get("title") or "").strip()
    context_text = str(article_context.get("context_text") or "").strip()
    summary = str(article_context.get("summary") or "").strip()
    heading_path = (selection_context or {}).get("heading_path") or []
    has_heading_path = isinstance(heading_path, list) and any(str(item).strip() for item in heading_path)
    if not any([selected_text, title, context_text, summary, has_heading_path]):
        return query
    if not selected_text:
        parts = [f"问题：\n{query}"]
    else:
        parts = [
            f"选区：\n{selected_text[:MAX_SELECTION_SEARCH_CHARS]}",
            f"问题：\n{query}",
        ]
    if title:
        parts.append(f"文章标题：\n{_clip_text(title, 160)}")
    if isinstance(heading_path, list) and heading_path:
        path = " > ".join(str(item) for item in heading_path if str(item).strip())
        if path:
            parts.append(f"标题路径：\n{_clip_text(path, 240)}")
    if context_text:
        parts.append(f"当前文章上下文：\n{_clip_text(context_text, 1200)}")
    if summary and article_context.get("context_source") != "full_article":
        parts.append(f"文章摘要：\n{_clip_text(summary, 500)}")
    return "\n\n".join(parts)


def _normalized_history_messages(history: list[dict[str, str]] | None) -> list[dict[str, str]]:
    if not isinstance(history, list):
        return []
    messages: list[dict[str, str]] = []
    for item in history[-WEB_CONTEXT_HISTORY_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = _clip_text(str(item.get("content") or ""), WEB_CONTEXT_HISTORY_MESSAGE_CHARS)
        if content:
            messages.append({"role": role, "content": content})
    return messages


def _history_prompt_block(history: list[dict[str, str]], history_summary: str) -> str:
    rows = [
        {
            "role": item.get("role"),
            "content": _clip_text(str(item.get("content") or ""), 900),
        }
        for item in history
        if str(item.get("content") or "").strip()
    ]
    return "\n".join(
        [
            f"长期摘要：{_clip_text(history_summary, WEB_CONTEXT_HISTORY_SUMMARY_CHARS)}",
            f"最近历史：{json.dumps(rows, ensure_ascii=False)}",
        ]
    )


def _direct_article_plan(mode: str) -> dict[str, Any]:
    return {
        "intent_label": "direct_article",
        "needs_web": False,
        "mode": mode,
        "subject": {},
        "selection_role": "context_only",
        "risk_level": "low",
        "freshness_sensitive": False,
        "answer_depth": "normal",
        "queries": [],
        "answer_claims_needed": [],
        "claim_requirements": {},
        "evidence_contract": {},
        "source_preferences": {},
        "answer_contract": {},
        "privacy": {"may_use_selection_text": False, "allowed_context_terms": []},
        "plan_status": "direct_no_planner",
    }


def _updated_history_summary(
    *,
    history_summary: str,
    history_summary_version: int,
    history_overflow: list[dict[str, str]] | None,
    warnings: list[str],
) -> dict[str, Any]:
    overflow = _normalized_history_messages_for_summary(history_overflow)
    if not overflow:
        return {}
    try:
        next_summary = _summarize_history(history_summary, overflow)
    except Exception as exc:
        warnings.append(f"长期记忆压缩失败，已使用最近历史继续：{exc}")
        return {
            "history_summary_failed": True,
            "history_summary_error": _clip_text(str(exc), 240),
        }
    return {
        "updated_history_summary": next_summary,
        "updated_history_summary_version": _coerced_summary_version(history_summary_version) + 1,
    }


def _normalized_history_messages_for_summary(history: list[dict[str, str]] | None) -> list[dict[str, str]]:
    if not isinstance(history, list):
        return []
    messages: list[dict[str, str]] = []
    for item in history[-WEB_CONTEXT_HISTORY_OVERFLOW_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = _clip_text(str(item.get("content") or ""), 1200)
        if content:
            messages.append({"role": role, "content": content})
    return messages


def _coerced_summary_version(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _summarize_history(history_summary: str, overflow: list[dict[str, str]]) -> str:
    prompt = "\n".join(
        [
            "你是 Hermes 文章侧边栏 AI 的长期记忆压缩器。",
            "把旧摘要和新增成功问答压缩为自然语言摘要，最多约 1000 个中文字符。",
            "保留讨论主题、已确认结论、重要实体/术语和未解决问题。",
            "不要保留用户偏好、完整用户原话、reasoning_trace、research trace、sources 或 warnings。",
            "不要写来源边界，不要解释压缩过程，只输出摘要正文。",
            f"旧摘要：{_clip_text(history_summary, WEB_CONTEXT_HISTORY_SUMMARY_CHARS)}",
            "新增成功问答：",
            json.dumps(overflow, ensure_ascii=False),
        ]
    )
    summary = _call_text_llm(
        prompt,
        timeout_seconds=WEB_CONTEXT_SUMMARY_TIMEOUT_SECONDS,
        max_tokens=900,
    )
    summary = summary.strip()
    if not summary:
        raise RuntimeError("summary provider returned empty content")
    return _clip_text(summary, WEB_CONTEXT_HISTORY_SUMMARY_CHARS)


def _reasoning_trace_from_capture(capture: dict[str, Any]) -> dict[str, Any]:
    value = capture.get("api_reasoning")
    if value:
        return {"source": "api", "api_reasoning": value}
    return {
        "source": "missing",
        "fallback_reason": "模型未返回可展示推理字段。",
    }


def _prioritize_article_sources(
    sources: list[dict[str, Any]],
    *,
    article_context: dict[str, Any],
    selection_context: dict[str, Any],
) -> list[dict[str, Any]]:
    if not sources or not _selection_needs_article_priority(selection_context):
        return sources
    return sorted(
        sources,
        key=lambda source: (
            0 if _source_matches_article(source, article_context) else 1,
            1 if _is_ai_answer_note_source(source) else 0,
        ),
    )


def _selection_needs_article_priority(selection_context: dict[str, Any]) -> bool:
    selected = str((selection_context or {}).get("selected_text") or "").strip()
    if not selected:
        return False
    if len(selected) <= 80:
        return True
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_./+-]{1,80}", selected))


def _source_matches_article(source: dict[str, Any], article_context: dict[str, Any]) -> bool:
    article_path = str(article_context.get("file_path") or article_context.get("path") or "").strip()
    source_path = str(source.get("file_path") or source.get("path") or "").strip()
    if article_path and source_path and article_path == source_path:
        return True
    article_id = str(article_context.get("id") or "").strip()
    source_id = str(source.get("id") or "").strip()
    if article_id and source_id and article_id == source_id:
        return True
    article_title = str(article_context.get("title") or "").strip()
    source_title = str(source.get("title") or "").strip()
    return bool(article_title and source_title and article_title == source_title)


def _is_ai_answer_note_source(source: dict[str, Any]) -> bool:
    if str(source.get("reading_session_id") or "").strip():
        return True
    if str(source.get("reading_draft_id") or "").strip():
        return True
    kind = str(source.get("source_kind") or source.get("type") or "").strip().lower()
    return kind in {"ai 解释", "ai explanation", "reading_draft", "reading draft", "answer_draft"}


def _query_cap(mode: str, plan: dict[str, Any]) -> int:
    if mode in {"web_only", "local_web"} and plan.get("needs_web"):
        return max(1, min(_max_initial_search_queries_for_plan(plan), MAX_WEB_QUERIES))
    return 0


def _mode_needs_local(mode: str) -> bool:
    return mode in {"local_only", "local_web"}


def _mode_needs_web(mode: str, plan: dict[str, Any]) -> bool:
    if mode == "web_only":
        return True
    if mode == "local_web":
        return bool(plan.get("needs_web"))
    return False


def _skipped_query_statuses(queries: list[str], *, reason: str) -> list[dict[str, Any]]:
    return [
        {
            "query": query,
            "status": "skipped",
            "duration_ms": 0,
            "result_count": 0,
            "error": reason,
        }
        for query in queries
    ]


def _search_candidates(
    queries: list[str],
    search_adapter: ResearchSearchAdapter,
    warnings: list[str],
    *,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    query_statuses: list[dict[str, Any]] = []
    seen: set[str] = set()
    for search_query in queries[:MAX_WEB_QUERIES]:
        _progress(progress_callback, "web_query_start", query=search_query)
        query_started = time.monotonic()
        try:
            raw = _search_with_timeout(search_adapter, search_query, WEB_CONTEXT_RESULTS_PER_QUERY)
            parsed = _parse_web_search(raw)
            query_status = {
                "query": search_query,
                "status": "success",
                "duration_ms": int((time.monotonic() - query_started) * 1000),
                "result_count": len(parsed),
                "error": "",
            }
        except Exception as exc:
            query_status = {
                "query": search_query,
                "status": "timeout" if isinstance(exc, TimeoutError) else "failed",
                "duration_ms": int((time.monotonic() - query_started) * 1000),
                "result_count": 0,
                "error": str(exc),
            }
            query_statuses.append(query_status)
            _progress(
                progress_callback,
                "web_query_error",
                query=search_query,
                error="搜索超时" if query_status["status"] == "timeout" else "搜索失败",
                query_status=query_status,
            )
            continue
        query_statuses.append(query_status)
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
        _progress(
            progress_callback,
            "web_query_done",
            query=search_query,
            result_count=len(parsed),
            candidate_count=len(candidates),
            query_status=query_status,
            web_sources=_trace_context_sources(candidates),
        )
    return candidates, query_statuses


def _search_with_timeout(
    search_adapter: ResearchSearchAdapter,
    query: str,
    limit: int,
) -> str | dict[str, Any]:
    timeout_seconds = _web_context_search_query_timeout_seconds()
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(search_adapter, query, limit)
    try:
        return future.result(timeout=timeout_seconds)
    except FuturesTimeoutError as exc:
        future.cancel()
        raise TimeoutError(f"搜索超时：{query}") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _web_context_search_query_timeout_seconds() -> float:
    raw = os.environ.get("HERMES_WEB_CONTEXT_SEARCH_QUERY_TIMEOUT_SECONDS")
    try:
        value = float(raw) if raw is not None else DEFAULT_WEB_CONTEXT_SEARCH_QUERY_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        value = DEFAULT_WEB_CONTEXT_SEARCH_QUERY_TIMEOUT_SECONDS
    return max(3.0, min(value, 45.0))


def _query_warning_messages(
    query_statuses: list[dict[str, Any]],
    source_count: int,
    *,
    has_existing_material: bool = True,
) -> list[str]:
    timeout_count = sum(1 for item in query_statuses if item.get("status") == "timeout")
    failure_count = sum(1 for item in query_statuses if item.get("status") == "failed")
    messages: list[str] = []
    if source_count:
        suffix = f"已用 {source_count} 个可用来源继续回答。"
    elif has_existing_material:
        suffix = "暂无可用联网来源，已用现有材料继续回答。"
    else:
        suffix = "暂无可用联网来源。"
    if timeout_count:
        messages.append(f"{timeout_count} 条查询超时，已跳过；{suffix}")
    if failure_count:
        messages.append(f"{failure_count} 条查询失败，已跳过；{suffix}")
    return messages


def _selection_context_prompt_block(
    selection_context: dict[str, Any],
    *,
    selected_limit: int,
    surrounding_limit: int,
) -> str:
    heading_path = selection_context.get("heading_path") or []
    if not isinstance(heading_path, list):
        heading_path = []
    lines = [
        f"当前选区：{_clip_text(str(selection_context.get('selected_text') or ''), selected_limit)}",
        f"选区前文：{_clip_text(str(selection_context.get('surrounding_before') or ''), surrounding_limit)}",
        f"选区后文：{_clip_text(str(selection_context.get('surrounding_after') or ''), surrounding_limit)}",
        f"标题路径：{' > '.join(str(item) for item in heading_path if str(item).strip())}",
    ]
    return "\n".join(lines)


def _article_context_prompt_block(article_context: dict[str, Any], *, body_limit: int) -> str:
    context_text = str(article_context.get("context_text") or article_context.get("body") or "")
    tags = article_context.get("tags") if isinstance(article_context.get("tags"), list) else []
    lines = [
        f"文章标题：{_clip_text(str(article_context.get('title') or ''), 160)}",
        f"文件夹：{_clip_text(str(article_context.get('folder_path') or ''), 160)}",
        f"摘要：{_clip_text(str(article_context.get('summary') or ''), 800)}",
        f"标签：{_clip_text(', '.join(str(item) for item in tags), 240)}",
        f"文件路径：{_clip_text(str(article_context.get('file_path') or ''), 240)}",
        f"文章上下文来源：{article_context.get('context_source') or 'unknown'}",
        f"文章上下文：{_clip_text(context_text, body_limit)}",
    ]
    return "\n".join(lines)


def _select_materials(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    local_answer: str,
    article_context: dict[str, Any],
    selection_context: dict[str, Any],
) -> dict[str, Any]:
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
            _selection_context_prompt_block(selection_context, selected_limit=240, surrounding_limit=500),
            _article_context_prompt_block(article_context, body_limit=1200),
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
    research_plan: dict[str, Any],
    local_answer: str,
    knowledge_sources: list[dict[str, Any]],
    web_sources: list[dict[str, Any]],
    web_queries: list[str],
    material_plan: dict[str, Any],
    history: list[dict[str, str]],
    history_summary: str,
) -> dict[str, Any]:
    return {
        "query": query,
        "mode": mode,
        "article_context": article_context,
        "selection_context": selection_context,
        "history": history,
        "history_summary": history_summary,
        "research_plan": research_plan,
        "local_answer": local_answer,
        "knowledge_sources": knowledge_sources,
        "web_sources": web_sources,
        "web_queries": web_queries,
        "material_plan": material_plan,
        "used_local": bool(local_answer or knowledge_sources),
        "used_web": bool(web_sources),
    }


def _generate_answer(
    payload: dict[str, Any],
    warnings: list[str],
    *,
    answer_delta_callback: AnswerDeltaCallback | None = None,
    reasoning_capture: dict[str, Any] | None = None,
) -> str:
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
    selection_block = _selection_context_prompt_block(payload.get("selection_context") or {}, selected_limit=600, surrounding_limit=1200)
    article_block = _article_context_prompt_block(payload.get("article_context") or {}, body_limit=4000)
    history_block = _history_prompt_block(
        list(payload.get("history") or []),
        str(payload.get("history_summary") or ""),
    )
    prompt = "\n".join(
        [
            "你是知识工作台里的文章理解辅助模块。",
            *WEB_CONTEXT_ANSWER_STYLE_CONTRACT_LINES,
            *_direct_answer_context_contract_lines(str(payload.get("mode") or "")),
            "如果用户问的是短词、英文术语或指代性问题，必须优先解释它在当前文章、当前选区和前后文里的具体含义；不要只给通用词典释义。",
            "即使当前选区只有一个英文词、缩写或术语，也要把它当作有效选区；不要说看不到选中内容，也不要要求用户重新框选。",
            "可以参考长期摘要和最近历史回答上下文追问，但本轮选区只以当前选区字段为准；不要自动继承上一轮选区。",
            "如果本地材料明显不能回答用户问题，应直接忽略，不要解释那些不相关材料在讲什么。",
            "优先识别一手来源、官方说明、媒体报道、论坛/博客各自的用途；弱来源可以作为背景或观点材料，但不要包装成官方结论。",
            "引用只能使用给定编号，如 [W1] 或 [K1]。",
            "如果当前模式是 current_article_only，不要输出任何 [字母+数字] 形式的来源编号，也不要声称做了知识库检索或联网搜索。",
            "如果联网材料参与回答，应在对应结论附近使用可用 [W] 引用；不要只在文末泛泛列来源。",
            f"当前模式：{payload.get('mode')}",
            f"用户问题：{payload['query']}",
            history_block,
            selection_block,
            article_block,
            f"本地材料：{_clip_text(str(payload.get('local_answer') or ''), 1000)}",
            f"本地来源：{json.dumps(knowledge_rows, ensure_ascii=False)}",
            f"网页材料：{json.dumps(source_rows, ensure_ascii=False)}",
        ]
    )
    try:
        if answer_delta_callback is not None:
            return _call_text_llm_stream(
                prompt,
                timeout_seconds=WEB_CONTEXT_SYNTHESIS_TIMEOUT_SECONDS,
                answer_delta_callback=answer_delta_callback,
                reasoning_capture=reasoning_capture,
            )
        return _call_text_llm(prompt, timeout_seconds=WEB_CONTEXT_SYNTHESIS_TIMEOUT_SECONDS)
    except Exception as exc:
        warnings.append(f"回答生成失败，已使用材料摘要：{exc}")
        return _fallback_answer(payload)


def _direct_answer_context_contract_lines(mode: str) -> list[str]:
    if mode != "current_article_only":
        return []
    return [
        "直出模式上下文优先级：本轮问题 > 本轮选区 > 最近历史 > 长期摘要 > 当前文章。",
        "最近历史里的 [当轮选区: ...] 是历史选区，不是本轮选区；只能用于理解追问指代。",
        "如果本轮没有选区且用户用“这些、他们、它、上面、刚才、区别、联系、表格”等方式追问，优先根据最近历史判断指代对象。",
        "如果最近历史仍不足以判断指代对象，要直接说明不确定并请用户补充，不要跳到文章里的其他主题。",
    ]


def _has_answer_material(payload: dict[str, Any]) -> bool:
    if payload.get("web_sources"):
        return True
    return _has_existing_answer_material(
        local_answer=str(payload.get("local_answer") or ""),
        knowledge_sources=list(payload.get("knowledge_sources") or []),
        article_context=payload.get("article_context")
        if isinstance(payload.get("article_context"), dict)
        else {},
        selection_context=payload.get("selection_context")
        if isinstance(payload.get("selection_context"), dict)
        else {},
    )


def _has_synthesis_material(payload: dict[str, Any]) -> bool:
    if payload.get("mode") == "web_only":
        return bool(payload.get("web_sources"))
    return _has_answer_material(payload)


def _has_existing_answer_material(
    *,
    local_answer: str,
    knowledge_sources: list[dict[str, Any]],
    article_context: dict[str, Any],
    selection_context: dict[str, Any],
) -> bool:
    if knowledge_sources or local_answer.strip():
        return True
    article_text = (
        article_context.get("context_text") or article_context.get("body") or article_context.get("summary") or ""
    )
    if str(article_text).strip():
        return True
    return bool(str(selection_context.get("selected_text") or "").strip())


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


def _call_text_llm_stream(
    prompt: str,
    *,
    timeout_seconds: float,
    answer_delta_callback: AnswerDeltaCallback,
    reasoning_capture: dict[str, Any] | None = None,
    max_tokens: int = 1600,
) -> str:
    config = load_config(str(Path(__file__).resolve().parent.parent / "config.yaml"))
    if not config.llm_api_key:
        raise RuntimeError("LLM API key is not configured")
    headers = {
        "Authorization": f"Bearer {config.llm_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": config.llm_model,
        "messages": [
            {"role": "system", "content": "你是 Hermes Knowledge 的联网辅助理解模块。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": max(1200, min(int(max_tokens or 1600), 6000)),
        "stream": True,
    }
    pieces: list[str] = []
    with httpx.Client(timeout=max(1.0, float(timeout_seconds or WEB_CONTEXT_SYNTHESIS_TIMEOUT_SECONDS))) as client:
        with client.stream(
            "POST",
            f"{config.llm_base_url or 'https://api.deepseek.com'}/chat/completions",
            headers=headers,
            json=body,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                data = line.removeprefix("data:").strip()
                if not data or data == "[DONE]":
                    if data == "[DONE]":
                        break
                    continue
                payload = json.loads(data)
                choice = (payload.get("choices") or [{}])[0]
                delta_payload = choice.get("delta") or {}
                reasoning_value = _extract_reasoning_value(delta_payload) or _extract_reasoning_value(choice)
                if reasoning_value:
                    _append_reasoning_capture(reasoning_capture, reasoning_value)
                delta = delta_payload.get("content") or ""
                if not delta:
                    continue
                pieces.append(str(delta))
                answer_delta_callback(str(delta))
    answer = "".join(pieces).strip()
    if not answer:
        raise RuntimeError("empty streamed response")
    return answer


def _extract_reasoning_value(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return None
    for key in ("reasoning", "reasoning_content", "reasoning_summary", "thoughts", "trace"):
        value = payload.get(key)
        if value:
            return value
    return None


def _append_reasoning_capture(capture: dict[str, Any] | None, value: Any) -> None:
    if capture is None:
        return
    if isinstance(value, str):
        previous = capture.get("api_reasoning")
        capture["api_reasoning"] = f"{previous or ''}{value}"
        return
    capture["api_reasoning"] = value


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

    return ANSWER_REF_RE.sub(replace, answer or "").strip()


def _strip_answer_refs(answer: str) -> str:
    return re.sub(r"\s*\\?\[[A-Za-z]{1,3}\d+\\?\]", "", answer or "").strip()


def _normalize_answer_refs(answer: str) -> str:
    return ANSWER_REF_RE.sub(lambda match: f"[{match.group(1).upper()}]", answer or "")


def _remove_process_language(answer: str) -> str:
    text = (answer or "").strip()
    if not text:
        return text
    lines = [line for line in text.splitlines() if not PROCESS_LANGUAGE_LINE_RE.match(line)]
    text = "\n".join(lines).strip()
    previous = None
    while text and previous != text:
        previous = text
        text = PROCESS_LANGUAGE_PREFIX_RE.sub("", text).lstrip(" ，,：:").strip()
    return text


def _cited_sources(
    answer: str,
    knowledge_sources: list[dict[str, Any]],
    web_sources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized = _normalize_answer_refs(answer)
    refs = {match.group(1).upper() for match in ANSWER_REF_RE.finditer(normalized)}
    if not refs:
        return knowledge_sources, web_sources
    return (
        [source for source in knowledge_sources if str(source.get("ref") or "").upper() in refs],
        [source for source in web_sources if str(source.get("ref") or "").upper() in refs],
    )


def _merge_sources_by_ref(
    preferred: list[dict[str, Any]],
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in [*preferred, *fallback]:
        ref = str(source.get("ref") or "").strip().upper()
        url = str(source.get("url") or "").strip().lower()
        key = ref or url
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(source)
    return merged


def _source_map_items(sources: list[dict[str, Any]], *, source_type: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in sources:
        ref = str(source.get("ref") or "").strip()
        item = {
            "ref": ref,
            "role": "context",
            "type": source_type,
            "id": str(source.get("id") or source.get("url") or ref),
            "title": str(source.get("title") or ref),
            "url": str(source.get("url") or ""),
            "path": str(source.get("path") or source.get("file_path") or source.get("url") or ""),
            "file_path": str(source.get("file_path") or source.get("path") or ""),
            "section": str(source.get("section") or source.get("section_title") or ""),
            "excerpt": str(source.get("excerpt") or source.get("description") or ""),
            "domain": str(source.get("domain") or ""),
            "status": source.get("extract_status") or "",
        }
        for key in ("source_kind", "reading_session_id", "reading_draft_id"):
            value = str(source.get(key) or "")
            if value:
                item[key] = value
        output.append(item)
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
    query_statuses: list[dict[str, Any]],
    web_attempted: bool,
    synthesis_skipped: bool,
) -> dict[str, Any]:
    status = {
        "path": "web_context",
        "total_elapsed_ms": int((time.monotonic() - started) * 1000),
        "warnings": list(warnings),
        "stage_timings": stage_timings,
        "synthesis_skipped": synthesis_skipped,
        "query_statuses": query_statuses,
    }
    if web_attempted:
        status.update(
            {
                "search_backend": _default_search_backend(),
                "extract_backend": "",
                "search_elapsed_ms": int(stage_timings.get("search_ms") or 0),
                "candidate_count": len(candidates),
                "used_sources": len(web_sources),
                "opened_pages": len(opened_urls),
                "extracted_pages": sum(1 for source in web_sources if source.get("extract_status") == "full"),
                "summary_only_pages": sum(1 for source in web_sources if source.get("extract_status") == "summary"),
                "fallback_attempts": [],
            }
        )
    return status


def _empty_web_context_stage_timings() -> dict[str, int]:
    return {
        "planning_ms": 0,
        "local_search_ms": 0,
        "search_ms": 0,
        "extract_ms": 0,
        "synthesis_ms": 0,
    }


def _add_web_context_stage_timing(stage_timings: dict[str, Any], key: str, started: float) -> None:
    if key not in _empty_web_context_stage_timings():
        return
    stage_timings[key] = int(stage_timings.get(key) or 0) + int((time.monotonic() - started) * 1000)


def _run_coro_sync(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)


def _progress(progress_callback: ProgressCallback | None, event: str, **kwargs: Any) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(event, **kwargs)
    except Exception:
        return


async def _emit(event_callback: ResearchEventCallback | None, event: str, **kwargs: Any) -> None:
    if event_callback is None:
        return
    result = event_callback({"type": event, **kwargs})
    if asyncio.iscoroutine(result):
        await result
