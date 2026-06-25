"""Shared research bridge for local knowledge + controlled web research."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.source_registry import get_registry
from bridge.result_envelope import success_envelope
from bridge.research.types import (
    ResearchSearchAdapter,
    ResearchExtractAdapter,
    ResearchSynthesizeAdapter,
    ResearchQueryPlannerAdapter,
    ResearchEventCallback,
    ResearchEvidenceCriticAdapter,
    VALID_MODES,
    MAX_WEB_RESULTS,
    MAX_EXTRACT_URLS,
    MAX_DYNAMIC_EXTRACT_URLS,
    MAX_WEB_QUERIES,
    MAX_WEB_CANDIDATES,
    WEB_SEARCH_TIMEOUT_SECONDS,
    PLANNER_LLM_TIMEOUT_SECONDS,
    EVIDENCE_CRITIC_TIMEOUT_SECONDS,
    MINIMAL_EVIDENCE_CRITIC_TIMEOUT_SECONDS,
    SYNTHESIS_LLM_TIMEOUT_SECONDS,
    RESEARCH_ANSWER_TIMEOUT_SECONDS,
    WEB_FALLBACK_MIN_REMAINING_SECONDS,
    EVIDENCE_RETRY_MIN_REMAINING_SECONDS,
    PLANNER_RESERVE_SECONDS,
    WEB_SEARCH_RESERVE_SECONDS,
    WEB_SEARCH_MIN_POST_RESERVE_SECONDS,
    EVIDENCE_CRITIC_RESERVE_SECONDS,
    DIRECT_PROBE_TIMEOUT_SECONDS,
    DIRECT_PROBE_MIN_REMAINING_SECONDS,
    LOCAL_EXTRACT_TIMEOUT_SECONDS,
    LOCAL_EXTRACT_MAX_WORKERS,
    SEARCH_ONLY_EXTRACT_BACKENDS,
    FALLBACK_SEARCH_BACKENDS,
    MAX_FALLBACK_SEARCH_BACKENDS,
    _RESEARCH_DEADLINE,
    WEB_INTENT_RE,
    LOCAL_ONLY_RE,
    HIGH_RISK_RE,
    MARKET_TERMS_RE,
    MARKET_RECAP_RE,
    SELECTION_DEPENDENT_RE,
    SELECTION_INDEPENDENT_RE,
    ENTITY_EXPLAINER_RE,
    LAW_CONCEPT_RE,
    DEFAULT_RESEARCH_YEAR,
)
from bridge.research.utils import (
    _coerce_bool,
    _enum_value,
    _string_list,
    _allowed_usage_list,
    _clip_text,
    _unique,
    _unique_queries,
    _domain,
    _clean_web_url,
    _domain_in_list,
    _domain_matches_required_entity,
    _dedupe_web_results,
    _json_payload,
    _yaml_payload_or_raise,
    _parse_web_search,
    _parse_web_extract,
    _parse_web_extract_failures,
    _first_sentence,
    _current_date_for_prompt,
    _sanitize_answer_contract,
    _sanitize_claim_requirements,
    _sanitize_evidence_contract,
    _sanitize_source_preferences,
    _extract_core_terms,
    _required_query_terms,
    _search_query_from_private_context,
    _contains_cjk,
    _start_research_budget,
    _remaining_research_budget,
    _stage_timeout,
    _research_budget_available,
    _web_search_stage_timeout,
    _emit_research_event_async,
    _ensure_main_repo_on_path,
    _claim_requires_observed_metric_value,
    _source_has_observed_metric_value,
)
from bridge.research.synthesis import (
    _synthesize_answer,
    _build_synthesis_prompt,
    _synthesis_max_tokens,
    _call_llm,
    _trace_sources,
    _fallback_answer,
    _fallback_evidence_refs,
    _fallback_evidence_sentence,
    _fallback_source_text,
    _fallback_best_source_text,
    _fallback_text_score,
    _should_use_low_quality_fallback,
    _requires_source_backed_fallback,
    _low_quality_fallback_answer,
    _cited_refs,
    _normalize_tool_citation_brackets,
    _invalid_non_tool_source_markers,
    _looks_like_source_marker,
    _strip_non_tool_source_markers,
    _available_refs,
    _ensure_valid_citations,
    _answer_looks_source_refusal,
    _ensure_preferred_domain_citation,
    GENERIC_SOURCE_LABELS,
    _ensure_no_unavailable_source_mentions,
    _ensure_no_unsupported_metric_values,
    _payload_has_supported_metric_claim,
    _answer_has_metric_value,
    _strip_metric_value_lines,
    _line_has_metric_value,
    _source_mention_labels,
    _answer_mentions_label,
    _append_ref_to_first_sentence,
    _repair_invalid_citations,
    _first_repairable_ref,
    _source_supports_answer,
    _build_research_trace,
    _filter_cited_sources,
    _normalize_cited_sources_and_answer,
    _ensure_source_backed_final_answer,
    _requires_source_backed_final_answer,
    _renumber_sources,
    _rewrite_answer_refs,
    _source_map_item,
)
from bridge.research.timing import _add_stage_timing, _ensure_stage_timings
from bridge.research.search import (
    _normalize_mode,
    _no_web_sources_warning,
    should_search_web,
    _same_or_parent_domain,
    _is_language_dictionary_source,
    _source_identity_quality_tags,
    _mentioned_urls,
    _clean_mentioned_url,
    _direct_probe_budget_available,
    _direct_probe_timeout,
    _independent_source_count,
    _minimum_reliable_source_count,
    _empty_search_quality,
    _should_let_critic_judge_quality_failure,
    _is_low_quality_source,
    _is_generic_homepage_source,
    _is_weak_secondary_source,
    _min_recall_candidates_for_plan,
    _min_recall_domains_for_plan,
    _allows_interpretive_secondary_recall,
    _allows_secondary_evidence_candidates,
    _retrieval_repair_reason,
    _preferred_query_domains,
    _results_include_preferred_domain,
    _domain_matches_subject,
    _source_matches_kind,
    _mentioned_url_matches_plan,
    _results_include_source_kind,
    _subject_probe_slug_candidates,
    _query_slug_candidates,
    _preferred_domain_probe_urls,
    _subject_slug_candidates_for_direct_sources,
    _finance_glossary_probe_urls,
    _missing_required_preferred_source,
    _missing_minimum_reliable_sources,
    _missing_preferred_domain_adoption,
    _requires_preferred_source_quality,
    _normalized_evidence_claim_ids,
    _max_extract_urls_for_plan,
    _current_year,
    _web_sources_from_ranked,
    extract_ranked_web_sources,
    extract_ranked_web_sources_async,
)
from bridge.research.planner import (
    plan_web_queries,
    plan_research,
    detect_query_intent,
    _is_high_risk_query,
    _is_finance_fact_query,
    _is_recent_news_query,
    _question_depends_on_selection,
    _apply_mode_contract_to_plan,
    _normalized_search_query_limit,
    _limited_search_queries,
    _plan_requires_full_search_budget,
    _max_initial_search_queries_for_plan,
    _max_repair_search_queries_for_plan,
    _planned_research_plan,
    _query_planner_payload,
    _default_query_planner,
    _build_query_planner_prompt,
    _parse_research_plan,
    _research_plan_from_payload,
    _fallback_research_plan,
    _fallback_queries,
    _fallback_subject_from_selection,
    _query_needs_selected_subject,
    _strip_selection_wrappers,
    _sanitize_planned_queries,
    _query_anchor_candidates,
    _query_anchor_is_context_only,
    _queries_with_detailed_question_anchor,
    _queries_with_subject_anchor,
    _redact_private_query_context,
    _private_query_fragments,
    _source_preferences_with_query_domains,
    _source_preferences_with_subject_defaults,
    _evidence_contract_with_source_preference_defaults,
    _sanitize_subject,
    _claims_with_stable_subject_claim,
    _sanitize_privacy,
    _normalize_symbolic_term,
    _context_terms_for_query,
    _has_required_term_match,
    _source_title_or_url_contains_term,
)
from bridge.research.evidence import (
    _ranked_sources_for_evidence,
    _evidence_claims_for_plan,
    _web_provider_status,
    _apply_evidence_critic,
    _retry_evidence_critic_in_batches,
    _prefer_minimal_evidence_critic,
    _retry_evidence_critic_minimal,
    _repair_evidence_critic_minimal_batches,
    _repair_insufficient_adoption_with_critic,
    _repair_missing_claim_coverage_with_critic,
    _missing_supported_claims,
    _supported_claims,
    _unadopted_candidates,
    _prune_redundant_same_domain_adopted_sources,
    _should_prune_redundant_same_domain_adopted_sources,
    _unadopted_independent_candidates,
    _has_unadopted_independent_candidates,
    _merge_adopted_sources,
    _dedupe_sources_by_url,
    _apply_evidence_decisions_to_sources,
    _should_retry_broad_low_risk_claim,
    _authority_disallowed_by_contract,
    _source_quality_disallowed_by_contract,
    _source_title_conflicts_with_subject,
    _looks_like_short_cjk_name,
    _plan_allows_interpretive_commentary,
    _source_rejected_by_authority_contract,
    _source_rejected_by_quality_contract,
    _source_is_adjacent_for_definition_claim,
    _sort_sources_by_subject_title_priority,
    _subject_title_priority,
    _plan_is_definition_like,
    _title_is_adjacent_to_subject,
    _normalize_subject_title,
    _evidence_critic_payload,
    _compact_evidence_excerpt,
    _build_evidence_critic_prompt,
    _parse_evidence_critic_decisions,
    _parse_claim_support,
    _apply_claim_requirements_to_decision,
    _strict_metric_claim_lacks_observed_value,
    _authority_satisfies_requirement,
    _classify_ranked_sources,
    _annotate_evidence_sources,
    _decision_for_source,
    _evidence_decision_adopts,
    _summary_only_disallowed,
    _summary_only_can_enter_critic,
    _allows_failed_extract_summary_fallback,
    _allows_failed_extract_title_fallback,
    _source_is_stale_for_freshness_sensitive_plan,
    _source_with_evidence_decision,
    _source_rejected_by_contract,
    _apply_critic_rejections_to_classified,
)
from bridge.research.search_infra import (
    collect_web_search_results,
    _search_query_candidates,
    _friendly_search_error,
    collect_web_search_results_async,
    rank_web_sources,
    evaluate_search_quality,
    _rank_and_evaluate_results,
    _retrieval_health,
    rewrite_queries_for_low_quality_results,
    _queries_with_preferred_domains,
    _preferred_domain_limited_queries,
    _post_critic_repair_queries,
    _direct_retrieval_candidates,
    _direct_preferred_domain_candidates,
    _direct_source_kind_candidates,
    _direct_mentioned_url_candidates,
)
from bridge.research.orchestration import (
    WebResearchDeps,
    empty_web_research,
    run_web_research,
    run_web_research_async,
)
from bridge.research.results import web_research_result
from bridge.research.adapters import (
    _legacy_search_web as _adapter_legacy_search_web,
    _default_web_search as _adapter_default_web_search,
    _default_search_backend,
    _available_fallback_search_backends,
    _web_search_with_backend as _adapter_web_search_with_backend,
    _default_web_extract as _adapter_default_web_extract,
    _local_web_extract as _adapter_local_web_extract,
    _extract_readable_text,
    _run_coroutine_in_thread,
)


def _default_web_search(query: str, limit: int) -> str:
    return _adapter_default_web_search(query, limit)


def _web_search_with_backend(query: str, limit: int, backend: str) -> str | dict[str, Any]:
    return _adapter_web_search_with_backend(query, limit, backend)


def _fallback_search_adapter(backend: str) -> ResearchSearchAdapter:
    def _adapter(query: str, limit: int) -> str | dict[str, Any]:
        return _web_search_with_backend(query, limit, backend)

    return _adapter


def _local_web_extract(urls: list[str], *, extract_backend: str = "") -> dict[str, Any]:
    return _adapter_local_web_extract(urls, extract_backend=extract_backend)


def _default_web_extract(urls: list[str]) -> str | dict[str, Any]:
    return _adapter_default_web_extract(urls, local_extract=_local_web_extract)


def _legacy_search_web(
    query: str,
    warnings: list[str],
    *,
    search_adapter: ResearchSearchAdapter | None,
    extract_adapter: ResearchExtractAdapter | None,
) -> list[dict[str, Any]]:
    return _adapter_legacy_search_web(
        query,
        warnings,
        search_adapter=search_adapter or _default_web_search,
        extract_adapter=extract_adapter or _default_web_extract,
    )


import bridge.research.planner as _planner_module

# Save original planner function before patching
_original_default_query_planner = _planner_module._default_query_planner


# Wrapper for _default_query_planner that can be patched by tests.
# Uses a closure cell (_wrapper_ref) to hold its own identity, because
# LOAD_GLOBAL inside the function would pick up the monkeypatched value
# from research_bridge.__dict__, breaking the identity comparison.
def _make_query_planner_wrapper():
    _wrapper_ref = None  # will be set to the wrapper itself below

    def _wrapper(payload: dict[str, Any]) -> str | list[str] | dict[str, Any]:
        """Wrapper for planner._default_query_planner that can be patched by tests."""
        import bridge.research_bridge as _rb
        current = getattr(_rb, "_default_query_planner", None)
        if current is not None and current is not _wrapper_ref:
            return current(payload)
        return _original_default_query_planner(payload)

    _wrapper_ref = _wrapper
    return _wrapper


_default_query_planner = _make_query_planner_wrapper()

# Patch planner module to use our wrapper
_planner_module._default_query_planner = _default_query_planner


# Wrapper for _default_evidence_critic that can be patched by tests.
# Same closure-based pattern as the query planner wrapper above.
import bridge.research.evidence as _evidence_module
_original_default_evidence_critic = _evidence_module._default_evidence_critic


def _make_evidence_critic_wrapper():
    _wrapper_ref = None

    def _wrapper(payload: dict[str, Any]) -> str | dict[str, Any]:
        import bridge.research_bridge as _rb
        current = getattr(_rb, "_default_evidence_critic", None)
        if current is not None and current is not _wrapper_ref:
            return current(payload)
        return _original_default_evidence_critic(payload)

    _wrapper_ref = _wrapper
    return _wrapper


_default_evidence_critic = _make_evidence_critic_wrapper()
_evidence_module._default_evidence_critic = _default_evidence_critic


# Wrapper for _fetch_url_summary that can be patched by tests.
# Same closure-based pattern as the query planner wrapper above.
import bridge.research.search_infra as _search_infra_module
from bridge.research.search import _fetch_url_summary as _original_fetch_url_summary


def _make_fetch_url_summary_wrapper():
    _wrapper_ref = None

    def _wrapper(url: str):
        import bridge.research_bridge as _rb
        current = getattr(_rb, "_fetch_url_summary", None)
        if current is not None and current is not _wrapper_ref:
            return current(url)
        return _original_fetch_url_summary(url)

    _wrapper_ref = _wrapper
    return _wrapper


_fetch_url_summary = _make_fetch_url_summary_wrapper()
_search_infra_module._fetch_url_summary = _fetch_url_summary


# Domain scoring data moved to config/source_preferences.yaml —
# access via ``agents.source_registry.get_registry()``.


def research_answer(
    *,
    query: str,
    mode: str = "local_web",
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    export_file: bool = False,
    search_adapter: ResearchSearchAdapter | None = None,
    extract_adapter: ResearchExtractAdapter | None = None,
    synthesize_adapter: ResearchSynthesizeAdapter | None = None,
) -> dict[str, Any]:
    """Answer a research question with current article, local RAG, and/or web evidence."""
    normalized_mode = _normalize_mode(mode)
    clean_query = (query or "").strip()
    if not clean_query:
        return success_envelope(
            agent="research",
            intent="research_answer",
            chat_preview="请先输入要研究的问题。",
            meta={"query": query, "mode": normalized_mode},
        )
    _start_research_budget()

    article_context = article_context or {}
    selection_context = selection_context or {}
    research_plan = plan_research(
        clean_query,
        mode=normalized_mode,
        article_context=article_context,
        selection_context=selection_context,
    )
    use_article = bool(article_context)
    use_local = normalized_mode in {"local_only", "local_web"}
    use_web = normalized_mode in {"web_only", "local_web"} and bool(research_plan.get("needs_web"))

    warnings: list[str] = []
    local_answer = ""
    knowledge_sources: list[dict[str, Any]] = []
    if use_local:
        local_answer, knowledge_sources = _search_local(clean_query, warnings)

    web_sources: list[dict[str, Any]] = []
    web_research: dict[str, Any] = _empty_web_research(
        clean_query,
        article_context,
        selection_context,
        research_plan=research_plan,
    )
    if use_web:
        web_research = _run_web_research(
            clean_query,
            warnings,
            research_plan=research_plan,
            article_context=article_context,
            selection_context=selection_context,
            search_adapter=search_adapter,
            extract_adapter=extract_adapter,
        )
        web_sources = web_research["sources"]

    synthesis_input = {
        "query": clean_query,
        "mode": normalized_mode,
        "article_context": article_context,
        "selection_context": selection_context,
        "local_answer": local_answer,
        "knowledge_sources": knowledge_sources,
        "web_sources": web_sources,
        "web_queries": web_research["queries"],
        "research_plan": research_plan,
        "plan_status": research_plan.get("plan_status"),
        "search_quality": web_research["search_quality"],
        "rewritten_web_queries": web_research["rewritten_queries"],
        "provider_status": web_research["provider_status"],
        "retrieved_web_sources": web_research["retrieved_sources"],
        "clue_sources": web_research["clue_sources"],
        "rejected_sources": web_research["rejected_sources"],
        "used_article": use_article,
        "used_local": use_local,
        "used_web": bool(web_sources),
    }
    stage_timings = _ensure_stage_timings(synthesis_input["provider_status"])
    synthesis_started = time.monotonic()
    if _should_use_low_quality_fallback(synthesis_input):
        answer = _low_quality_fallback_answer(synthesis_input)
    else:
        answer = (
            synthesize_adapter(synthesis_input)
            if synthesize_adapter
            else _synthesize_answer(synthesis_input, warnings)
        )
    _add_stage_timing(stage_timings, "synthesis_ms", synthesis_started)
    answer = _ensure_valid_citations(answer, synthesis_input, warnings)
    answer = _ensure_preferred_domain_citation(answer, synthesis_input)
    answer = _ensure_no_unavailable_source_mentions(answer, synthesis_input, warnings)
    answer = _ensure_no_unsupported_metric_values(answer, synthesis_input, warnings)
    if use_web and not web_sources:
        warnings.append(_no_web_sources_warning(use_local=use_local, use_article=use_article))
    if research_plan.get("risk_level") == "high":
        warnings.append("该问题涉及高风险或强时效判断，请优先核对官方公告、原始报告或主流可靠来源；AI 综合不构成投资、法律或医疗建议。")

    answer, cited_knowledge_sources, cited_web_sources = _normalize_cited_sources_and_answer(
        answer,
        knowledge_sources,
        web_sources,
    )
    answer, cited_knowledge_sources, cited_web_sources = _ensure_source_backed_final_answer(
        answer,
        synthesis_input,
        cited_knowledge_sources,
        cited_web_sources,
        warnings,
    )
    synthesis_input["knowledge_sources"] = cited_knowledge_sources
    synthesis_input["web_sources"] = cited_web_sources
    synthesis_input["used_web"] = bool(cited_web_sources)
    source_map = [
        _source_map_item(source, "knowledge", index)
        for index, source in enumerate(cited_knowledge_sources, 1)
    ] + [
        _source_map_item(source, "web", index)
        for index, source in enumerate(cited_web_sources, 1)
    ]

    return success_envelope(
        agent="research",
        intent="research_answer",
        chat_preview=answer,
        source_map=source_map,
        warnings=_unique(warnings),
        meta={
            "query": clean_query,
            "mode": normalized_mode,
            "answer": answer,
            "knowledge_sources": cited_knowledge_sources,
            "web_sources": cited_web_sources,
            "retrieved_knowledge_sources": knowledge_sources,
            "retrieved_web_sources": web_research["retrieved_sources"],
            "clue_sources": web_research["clue_sources"],
            "rejected_sources": web_research["rejected_sources"],
            "web_queries": synthesis_input["web_queries"],
            "query_intent": research_plan.get("intent_label") or "",
            "research_plan": research_plan,
            "plan_status": synthesis_input["plan_status"],
            "search_quality": synthesis_input["search_quality"],
            "rewritten_web_queries": synthesis_input["rewritten_web_queries"],
            "provider_status": synthesis_input["provider_status"],
            "research_trace": _build_research_trace(synthesis_input, warnings),
            "used_article": use_article,
            "used_local": use_local,
            "used_web": bool(cited_web_sources),
            "web_search_attempted": use_web,
            "export_file": export_file,
        },
    )


async def async_research_answer(
    *,
    query: str,
    mode: str = "local_web",
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    export_file: bool = False,
    search_adapter: ResearchSearchAdapter | None = None,
    extract_adapter: ResearchExtractAdapter | None = None,
    synthesize_adapter: ResearchSynthesizeAdapter | None = None,
    event_callback: ResearchEventCallback | None = None,
) -> dict[str, Any]:
    """Async variant for FastAPI routes so web extraction can be awaited safely."""
    normalized_mode = _normalize_mode(mode)
    clean_query = (query or "").strip()
    if not clean_query:
        return success_envelope(
            agent="research",
            intent="research_answer",
            chat_preview="请先输入要研究的问题。",
            meta={"query": query, "mode": normalized_mode},
        )
    _start_research_budget()

    article_context = article_context or {}
    selection_context = selection_context or {}
    await _emit_research_event_async(
        event_callback,
        "planning_start",
        mode=normalized_mode,
    )
    research_plan = plan_research(
        clean_query,
        mode=normalized_mode,
        article_context=article_context,
        selection_context=selection_context,
    )
    await _emit_research_event_async(
        event_callback,
        "plan",
        mode=normalized_mode,
        plan_status=research_plan.get("plan_status"),
        needs_web=bool(research_plan.get("needs_web")),
        queries=list(research_plan.get("queries") or []),
        intent_label=research_plan.get("intent_label") or "",
    )
    use_article = bool(article_context)
    use_local = normalized_mode in {"local_only", "local_web"}
    use_web = normalized_mode in {"web_only", "local_web"} and bool(research_plan.get("needs_web"))

    warnings: list[str] = []
    local_answer = ""
    knowledge_sources: list[dict[str, Any]] = []
    if use_local:
        await _emit_research_event_async(event_callback, "local_search_start")
        local_answer, knowledge_sources = await asyncio.to_thread(_search_local, clean_query, warnings)
        await _emit_research_event_async(
            event_callback,
            "local_search_done",
            knowledge_sources=_trace_sources(knowledge_sources),
        )

    web_sources: list[dict[str, Any]] = []
    web_research: dict[str, Any] = _empty_web_research(
        clean_query,
        article_context,
        selection_context,
        research_plan=research_plan,
    )
    if use_web:
        await _emit_research_event_async(
            event_callback,
            "web_search_start",
            queries=list(research_plan.get("queries") or []),
        )
        web_research = await _run_web_research_async(
            clean_query,
            warnings,
            research_plan=research_plan,
            article_context=article_context,
            selection_context=selection_context,
            search_adapter=search_adapter,
            extract_adapter=extract_adapter,
            event_callback=event_callback,
        )
        web_sources = web_research["sources"]

    synthesis_input = {
        "query": clean_query,
        "mode": normalized_mode,
        "article_context": article_context,
        "selection_context": selection_context,
        "local_answer": local_answer,
        "knowledge_sources": knowledge_sources,
        "web_sources": web_sources,
        "web_queries": web_research["queries"],
        "research_plan": research_plan,
        "plan_status": research_plan.get("plan_status"),
        "search_quality": web_research["search_quality"],
        "rewritten_web_queries": web_research["rewritten_queries"],
        "provider_status": web_research["provider_status"],
        "retrieved_web_sources": web_research["retrieved_sources"],
        "clue_sources": web_research["clue_sources"],
        "rejected_sources": web_research["rejected_sources"],
        "used_article": use_article,
        "used_local": use_local,
        "used_web": bool(web_sources),
    }
    stage_timings = _ensure_stage_timings(synthesis_input["provider_status"])
    synthesis_started = time.monotonic()
    if _should_use_low_quality_fallback(synthesis_input):
        answer = _low_quality_fallback_answer(synthesis_input)
    else:
        await _emit_research_event_async(event_callback, "synthesis_start")
        answer = (
            synthesize_adapter(synthesis_input)
            if synthesize_adapter
            else await asyncio.to_thread(_synthesize_answer, synthesis_input, warnings)
        )
    _add_stage_timing(stage_timings, "synthesis_ms", synthesis_started)
    answer = _ensure_valid_citations(answer, synthesis_input, warnings)
    answer = _ensure_preferred_domain_citation(answer, synthesis_input)
    answer = _ensure_no_unavailable_source_mentions(answer, synthesis_input, warnings)
    answer = _ensure_no_unsupported_metric_values(answer, synthesis_input, warnings)
    if use_web and not web_sources:
        warnings.append(_no_web_sources_warning(use_local=use_local, use_article=use_article))
    if research_plan.get("risk_level") == "high":
        warnings.append("该问题涉及高风险或强时效判断，请优先核对官方公告、原始报告或主流可靠来源；AI 综合不构成投资、法律或医疗建议。")
    if warnings:
        await _emit_research_event_async(event_callback, "warnings", warnings=_unique(warnings))

    answer, cited_knowledge_sources, cited_web_sources = _normalize_cited_sources_and_answer(
        answer,
        knowledge_sources,
        web_sources,
    )
    warning_count_before_final_contract = len(warnings)
    answer, cited_knowledge_sources, cited_web_sources = _ensure_source_backed_final_answer(
        answer,
        synthesis_input,
        cited_knowledge_sources,
        cited_web_sources,
        warnings,
    )
    if len(warnings) > warning_count_before_final_contract:
        await _emit_research_event_async(event_callback, "warnings", warnings=_unique(warnings))
    synthesis_input["knowledge_sources"] = cited_knowledge_sources
    synthesis_input["web_sources"] = cited_web_sources
    synthesis_input["used_web"] = bool(cited_web_sources)
    source_map = [
        _source_map_item(source, "knowledge", index)
        for index, source in enumerate(cited_knowledge_sources, 1)
    ] + [
        _source_map_item(source, "web", index)
        for index, source in enumerate(cited_web_sources, 1)
    ]

    return success_envelope(
        agent="research",
        intent="research_answer",
        chat_preview=answer,
        source_map=source_map,
        warnings=_unique(warnings),
        meta={
            "query": clean_query,
            "mode": normalized_mode,
            "answer": answer,
            "knowledge_sources": cited_knowledge_sources,
            "web_sources": cited_web_sources,
            "retrieved_knowledge_sources": knowledge_sources,
            "retrieved_web_sources": web_research["retrieved_sources"],
            "clue_sources": web_research["clue_sources"],
            "rejected_sources": web_research["rejected_sources"],
            "used_article": use_article,
            "used_local": use_local,
            "used_web": bool(cited_web_sources),
            "web_queries": synthesis_input["web_queries"],
            "query_intent": research_plan.get("intent_label") or "",
            "research_plan": research_plan,
            "plan_status": synthesis_input["plan_status"],
            "search_quality": synthesis_input["search_quality"],
            "rewritten_web_queries": synthesis_input["rewritten_web_queries"],
            "provider_status": synthesis_input["provider_status"],
            "research_trace": _build_research_trace(synthesis_input, warnings),
            "web_search_attempted": use_web,
            "export_file": export_file,
        },
    )


from bridge.research_bridge_facade import (
    _empty_web_research,
    _run_web_research,
    _run_web_research_async,
    _search_local,
    _search_web,
    _search_web_async,
    _web_research_deps,
    _web_research_result,
)
