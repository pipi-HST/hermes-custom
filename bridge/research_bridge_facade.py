"""Compatibility facade helpers for bridge.research_bridge."""
from __future__ import annotations

from typing import Any

from bridge.research.types import (
    ResearchEventCallback,
    ResearchExtractAdapter,
    ResearchSearchAdapter,
)
from bridge.research.orchestration import (
    WebResearchDeps,
    empty_web_research,
    run_web_research,
    run_web_research_async,
)
from bridge.research.results import web_research_result


def _bridge_module():
    import bridge.research_bridge as research_bridge

    return research_bridge


def _search_local(query: str, warnings: list[str]) -> tuple[str, list[dict[str, Any]]]:
    try:
        from bridge.rag_bridge import retrieve_knowledge_sources

        bundle = retrieve_knowledge_sources(query)
    except Exception as exc:
        warnings.append(f"本地知识库检索失败：{exc}")
        return "", []

    source_map = bundle.get("source_map") if isinstance(bundle, dict) else []
    results = bundle.get("results") if isinstance(bundle, dict) else []
    sources: list[dict[str, Any]] = []
    if isinstance(source_map, list):
        for index, item in enumerate(source_map[:8], 1):
            if not isinstance(item, dict):
                continue
            raw = results[index - 1] if isinstance(results, list) and index <= len(results) else {}
            evidence = str(
                (raw or {}).get("body")
                or (raw or {}).get("story")
                or item.get("excerpt")
                or ""
            ).strip()
            sources.append(
                {
                    "ref": f"K{index}",
                    "type": "knowledge",
                    "id": str(item.get("id") or ""),
                    "title": str(item.get("title") or item.get("path") or f"知识来源 {index}"),
                    "path": str(item.get("path") or ""),
                    "file_path": str(item.get("file_path") or item.get("path") or ""),
                    "section": str(item.get("section") or ""),
                    # The web synthesizer gets first-hand section text, not the
                    # 500-char API excerpt.  The source_map stays short for UI.
                    "excerpt": evidence[:1200],
                    "source_kind": str(item.get("source_kind") or ""),
                    "reading_session_id": str(item.get("reading_session_id") or ""),
                    "reading_draft_id": str(item.get("reading_draft_id") or ""),
                    "score": item.get("score"),
                    "source_count": item.get("source_count"),
                    "citation_count": item.get("citation_count"),
                }
            )
    # No local answer is generated here: the whole point of the split is to let
    # the outer synthesizer read first-hand evidence exactly once.
    return "", sources


def _search_web(
    query: str,
    warnings: list[str],
    *,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    search_adapter: ResearchSearchAdapter | None,
    extract_adapter: ResearchExtractAdapter | None,
) -> list[dict[str, Any]]:
    return _run_web_research(
        query,
        warnings,
        article_context=article_context,
        selection_context=selection_context,
        search_adapter=search_adapter,
        extract_adapter=extract_adapter,
    )["sources"]


def _web_research_deps() -> WebResearchDeps:
    bridge = _bridge_module()
    return WebResearchDeps(
        default_web_search=bridge._default_web_search,
        default_web_extract=bridge._default_web_extract,
        default_search_backend=bridge._default_search_backend,
        available_fallback_search_backends=bridge._available_fallback_search_backends,
        fallback_search_adapter=bridge._fallback_search_adapter,
        research_budget_available=bridge._research_budget_available,
    )


def _empty_web_research(
    query: str,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    *,
    research_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return empty_web_research(
        query,
        article_context=article_context,
        selection_context=selection_context,
        research_plan=research_plan,
    )


def _run_web_research(
    query: str,
    warnings: list[str],
    *,
    research_plan: dict[str, Any] | None = None,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    search_adapter: ResearchSearchAdapter | None,
    extract_adapter: ResearchExtractAdapter | None,
) -> dict[str, Any]:
    return run_web_research(
        query,
        warnings,
        research_plan=research_plan,
        article_context=article_context,
        selection_context=selection_context,
        search_adapter=search_adapter,
        extract_adapter=extract_adapter,
        deps=_web_research_deps(),
    )


def _web_research_result(
    sources: list[dict[str, Any]],
    *,
    classified: dict[str, list[dict[str, Any]]],
    queries: list[str],
    research_plan: dict[str, Any],
    search_quality: dict[str, Any],
    rewritten_queries: list[str],
    provider_status: dict[str, Any],
) -> dict[str, Any]:
    return web_research_result(
        sources,
        classified=classified,
        queries=queries,
        research_plan=research_plan,
        search_quality=search_quality,
        rewritten_queries=rewritten_queries,
        provider_status=provider_status,
    )


async def _search_web_async(
    query: str,
    warnings: list[str],
    *,
    research_plan: dict[str, Any] | None = None,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    search_adapter: ResearchSearchAdapter | None,
    extract_adapter: ResearchExtractAdapter | None,
) -> list[dict[str, Any]]:
    return (await _run_web_research_async(
        query,
        warnings,
        research_plan=research_plan,
        article_context=article_context,
        selection_context=selection_context,
        search_adapter=search_adapter,
        extract_adapter=extract_adapter,
    ))["sources"]


async def _run_web_research_async(
    query: str,
    warnings: list[str],
    *,
    research_plan: dict[str, Any] | None = None,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    search_adapter: ResearchSearchAdapter | None,
    extract_adapter: ResearchExtractAdapter | None,
    event_callback: ResearchEventCallback | None = None,
) -> dict[str, Any]:
    return await run_web_research_async(
        query,
        warnings,
        research_plan=research_plan,
        article_context=article_context,
        selection_context=selection_context,
        search_adapter=search_adapter,
        extract_adapter=extract_adapter,
        event_callback=event_callback,
        deps=_web_research_deps(),
    )
