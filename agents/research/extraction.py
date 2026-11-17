"""Evidence extraction and critic pass for web research."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from agents.research.evidence import (
    _allows_failed_extract_summary_fallback,
    _allows_failed_extract_title_fallback,
    _annotate_evidence_sources,
    _apply_evidence_critic,
    _ranked_sources_for_evidence,
)
from agents.research.search import (
    extract_ranked_web_sources,
    extract_ranked_web_sources_async,
)
from agents.research.synthesis import _renumber_sources, _trace_sources
from agents.research.timing import _add_stage_timing
from agents.research.types import ResearchEventCallback, ResearchExtractAdapter
from agents.research.utils import _emit_research_event_async


def extract_and_criticize_sources(
    query: str,
    ranked: list[dict[str, Any]],
    research_plan: dict[str, Any],
    extract_adapter: ResearchExtractAdapter,
    warnings: list[str],
    *,
    extract_limit: int,
    stage_timings: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence_ranked = _ranked_sources_for_evidence(
        query,
        ranked,
        research_plan.get("source_preferences") or {},
        research_plan=research_plan,
        subject=research_plan.get("subject") or {},
        evidence_contract=research_plan.get("evidence_contract") or {},
    )
    extract_started = time.monotonic()
    sources = extract_ranked_web_sources(
        evidence_ranked,
        extract_adapter=extract_adapter,
        warnings=warnings,
        max_urls=extract_limit,
        allow_failed_summary=_allows_failed_extract_summary_fallback(research_plan),
        allow_failed_title_summary=_allows_failed_extract_title_fallback(research_plan),
    )
    _add_stage_timing(stage_timings, "extract_ms", extract_started)
    sources = _annotate_evidence_sources(query, sources, research_plan=research_plan)
    critic_started = time.monotonic()
    sources, critic_rejected_sources = _apply_evidence_critic(
        query,
        research_plan,
        sources,
        warnings,
        stage_timings=stage_timings,
    )
    _add_stage_timing(stage_timings, "critic_ms", critic_started)
    return _renumber_sources(sources, "W", {}), critic_rejected_sources


async def extract_and_criticize_sources_async(
    query: str,
    ranked: list[dict[str, Any]],
    research_plan: dict[str, Any],
    extract_adapter: ResearchExtractAdapter,
    warnings: list[str],
    *,
    extract_limit: int,
    event_callback: ResearchEventCallback | None,
    stage_timings: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence_ranked = _ranked_sources_for_evidence(
        query,
        ranked,
        research_plan.get("source_preferences") or {},
        research_plan=research_plan,
        subject=research_plan.get("subject") or {},
        evidence_contract=research_plan.get("evidence_contract") or {},
    )
    extract_started = time.monotonic()
    sources = await extract_ranked_web_sources_async(
        evidence_ranked,
        extract_adapter=extract_adapter,
        warnings=warnings,
        max_urls=extract_limit,
        allow_failed_summary=_allows_failed_extract_summary_fallback(research_plan),
        allow_failed_title_summary=_allows_failed_extract_title_fallback(research_plan),
    )
    _add_stage_timing(stage_timings, "extract_ms", extract_started)
    sources = _annotate_evidence_sources(query, sources, research_plan=research_plan)
    await _emit_research_event_async(
        event_callback,
        "web_critic_start",
        web_sources=_trace_sources(sources),
    )
    critic_started = time.monotonic()
    sources, critic_rejected_sources = await asyncio.to_thread(
        _apply_evidence_critic,
        query,
        research_plan,
        sources,
        warnings,
        stage_timings=stage_timings,
    )
    _add_stage_timing(stage_timings, "critic_ms", critic_started)
    return _renumber_sources(sources, "W", {}), critic_rejected_sources
