"""Evidence critic: evaluate, adopt, and reject web sources based on evidence contracts."""
from __future__ import annotations

import re
import time
from typing import Any

from bridge.research.types import (
    ENTITY_EXPLAINER_RE,
    EVIDENCE_RETRY_MIN_REMAINING_SECONDS,
    ResearchEvidenceCriticAdapter,
)
from bridge.research.timing import _add_stage_timing, _normalized_stage_timings
from bridge.research.utils import (
    _string_list,
    _clip_text,
    _unique_queries,
    _domain,
    _sanitize_claim_requirements,
    _sanitize_evidence_contract,
    _sanitize_source_preferences,
    _research_budget_available,
    _ensure_main_repo_on_path,
)
from bridge.research.search import (
    _independent_source_count,
    _is_generic_homepage_source,
    _is_low_quality_source,
    _is_weak_secondary_source,
    _minimum_reliable_source_count,
    _missing_minimum_reliable_sources,
    _normalized_evidence_claim_ids,
    _source_satisfies_preferences,
)
from bridge.research.evidence_critic_io import (
    _build_evidence_critic_prompt,
    _compact_evidence_excerpt,
    _default_evidence_critic,
    _evidence_critic_payload,
    _parse_claim_support,
    _parse_evidence_critic_decisions,
)
from bridge.research.evidence_critic_runner import (
    _prefer_minimal_evidence_critic,
    _repair_evidence_critic_minimal_batches,
    _retry_evidence_critic_in_batches,
    _retry_evidence_critic_minimal,
)
from bridge.research.evidence_classification import (
    _annotate_evidence_sources,
    _apply_critic_rejections_to_classified,
    _classify_ranked_sources,
    _looks_like_short_cjk_name,
    _normalize_subject_title,
    _plan_is_definition_like,
    _sort_sources_by_subject_title_priority,
    _source_is_adjacent_for_definition_claim,
    _source_matches_subject,
    _source_title_conflicts_with_subject,
    _subject_title_priority,
    _title_is_adjacent_to_subject,
)
from bridge.research.evidence_decisions import (
    _allows_failed_extract_summary_fallback,
    _allows_failed_extract_title_fallback,
    _apply_claim_requirements_to_decision,
    _authority_disallowed_by_contract,
    _authority_satisfies_requirement,
    _decision_for_source,
    _evidence_decision_adopts,
    _plan_allows_interpretive_commentary,
    _should_retry_broad_low_risk_claim,
    _source_is_stale_for_freshness_sensitive_plan,
    _source_quality_disallowed_by_contract,
    _source_rejected_by_authority_contract,
    _source_rejected_by_contract,
    _source_rejected_by_quality_contract,
    _source_with_evidence_decision,
    _strict_metric_claim_lacks_observed_value,
    _summary_only_can_enter_critic,
    _summary_only_disallowed,
)


def _ranked_sources_for_evidence(
    query: str,
    ranked: list[dict[str, Any]],
    source_preferences: dict[str, Any],
    *,
    research_plan: dict[str, Any] | None = None,
    subject: dict[str, Any] | None = None,
    evidence_contract: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    preferences = _sanitize_source_preferences(source_preferences or {})
    contract = _sanitize_evidence_contract(evidence_contract or {})
    plan_for_secondary = {
        **(research_plan or {}),
        "source_preferences": preferences,
        "evidence_contract": contract,
    }
    candidates = [
        source
        for source in ranked
        if source.get("url")
    ]
    if not candidates and _allows_failed_extract_summary_fallback(plan_for_secondary):
        candidates = [
            source
            for source in ranked
            if source.get("url")
        ]
    minimum = int(contract.get("minimum_independent_sources") or 1)
    if len(candidates) < minimum and _allows_failed_extract_summary_fallback(plan_for_secondary):
        candidates = _merge_adopted_sources(
            candidates,
            [
                source
                for source in ranked
                if source.get("url")
            ],
        )
    candidates = _sort_sources_by_subject_title_priority(candidates, subject or {})
    preferred = [
        source
        for source in candidates
        if _source_satisfies_preferences(source, preferences)
    ]
    if preferred:
        return _merge_adopted_sources(preferred, candidates)
    return candidates


def _evidence_claims_for_plan(
    query: str,
    research_plan: dict[str, Any],
    sources: list[dict[str, Any]],
) -> list[str]:
    explicit_claims = _normalized_evidence_claim_ids(_string_list(research_plan.get("answer_claims_needed"))[:8])
    if explicit_claims:
        return explicit_claims
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    preferences = _sanitize_source_preferences(research_plan.get("source_preferences") or {})
    if preferences.get("preferred_source_kinds") or int(contract.get("minimum_independent_sources") or 1) > 1:
        return ["answer_user_question"]
    if preferences.get("preferred_domains") or preferences.get("prefer_primary_sources"):
        return ["answer_user_question"]
    if any(source.get("fallback_candidate") for source in sources):
        return ["answer_user_question"]
    if any(
        _is_low_quality_source(source) or _is_weak_secondary_source(source) or _is_generic_homepage_source(source)
        for source in sources
    ):
        return ["answer_user_question"]
    return []


def _web_provider_status(
    started: float,
    warnings: list[str],
    warning_start: int,
    *,
    sources: list[dict[str, Any]] | None = None,
    retrieval_health: dict[str, Any] | None = None,
    fallback_attempts: list[dict[str, Any]] | None = None,
    stage_timings: dict[str, Any] | None = None,
    query_statuses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    search_backend = ""
    extract_backend = ""
    try:
        _ensure_main_repo_on_path()
        from tools import web_tools

        search_backend = str(web_tools._get_search_backend() or "")
        extract_backend = str(web_tools._get_extract_backend() or "")
    except Exception:
        pass
    extracted = sources or []
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "search_backend": search_backend,
        "extract_backend": extract_backend,
        "search_elapsed_ms": elapsed_ms,
        "total_elapsed_ms": elapsed_ms,
        "stage_timings": _normalized_stage_timings(stage_timings),
        "query_statuses": list(query_statuses or []),
        "warnings": warnings[warning_start:],
        "extracted_pages": sum(1 for source in extracted if source.get("extract_status") == "full"),
        "summary_only_pages": sum(1 for source in extracted if source.get("extract_status") == "summary"),
        "retrieval_health": retrieval_health or {},
        "fallback_attempts": fallback_attempts or [],
    }


def _apply_evidence_critic(
    query: str,
    research_plan: dict[str, Any],
    sources: list[dict[str, Any]],
    warnings: list[str],
    *,
    critic_adapter: ResearchEvidenceCriticAdapter | None = None,
    stage_timings: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    claims = _evidence_claims_for_plan(query, research_plan, sources)
    if not claims or not sources:
        return sources, []
    adapter = critic_adapter or _default_evidence_critic
    prefer_minimal = _prefer_minimal_evidence_critic(research_plan, claims)
    used_minimal_batches = bool(prefer_minimal and len(sources) > 2)
    used_full_batches = bool(not prefer_minimal and len(sources) > 3)
    if used_minimal_batches:
        decisions = _retry_evidence_critic_minimal(query, research_plan, sources, claims, adapter, stage_timings=stage_timings)
        if decisions:
            warnings.append("已用轻量分批证据裁判复核候选来源。")
        else:
            decisions = _retry_evidence_critic_in_batches(query, research_plan, sources, claims, adapter, stage_timings=stage_timings)
            if decisions:
                warnings.append("轻量证据裁判失败，已改用完整分批裁判复核候选来源。")
            else:
                warnings.append("证据裁判失败，已拒绝采用未经裁判的网页证据。")
                return [], [_source_rejected_by_contract(source, "evidence_critic_failed") for source in sources]
    elif used_full_batches:
        decisions = _retry_evidence_critic_in_batches(query, research_plan, sources, claims, adapter, stage_timings=stage_timings)
        if decisions:
            warnings.append("已用分批证据裁判复核候选来源。")
        else:
            warnings.append("证据裁判失败，已拒绝采用未经裁判的网页证据。")
            return [], [_source_rejected_by_contract(source, "evidence_critic_failed") for source in sources]
    else:
        initial_started = time.monotonic()
        try:
            payload = _evidence_critic_payload(query, research_plan, sources, claims)
            if prefer_minimal:
                payload["minimal_critic"] = True
            raw = adapter(payload)
            decisions = _parse_evidence_critic_decisions(raw)
        except Exception as exc:
            _add_stage_timing(stage_timings, "initial_critic_ms", initial_started)
            decisions = (
                _retry_evidence_critic_minimal(query, research_plan, sources, claims, adapter, stage_timings=stage_timings)
                if prefer_minimal
                else _retry_evidence_critic_in_batches(query, research_plan, sources, claims, adapter, stage_timings=stage_timings)
            )
            if not decisions and prefer_minimal:
                decisions = _retry_evidence_critic_in_batches(query, research_plan, sources, claims, adapter, stage_timings=stage_timings)
            if decisions:
                warnings.append(f"证据裁判首轮失败，已分批复核候选来源：{exc}")
            else:
                adopted, rejected = _timed_limited_summary_adoption_for_uncertified_critic(
                    query,
                    research_plan,
                    sources,
                    claims,
                    stage_timings,
                )
                if adopted:
                    warnings.append(f"证据裁判失败，已按低风险摘要证据合同有限采用直接匹配来源：{exc}")
                    return adopted, rejected
                warnings.append(f"证据裁判失败，已拒绝采用未经裁判的网页证据：{exc}")
                return [], [_source_rejected_by_contract(source, "evidence_critic_failed") for source in sources]
        else:
            _add_stage_timing(stage_timings, "initial_critic_ms", initial_started)
    if not decisions:
        decisions = _retry_evidence_critic_minimal(query, research_plan, sources, claims, adapter, stage_timings=stage_timings)
        if decisions:
            warnings.append("证据裁判首轮未返回有效决策，已用极简裁判提示复核候选来源。")
        else:
            decisions = _retry_evidence_critic_in_batches(query, research_plan, sources, claims, adapter, stage_timings=stage_timings)
            if decisions:
                warnings.append("证据裁判首轮未返回有效决策，已分批复核候选来源。")
            else:
                adopted, rejected = _timed_limited_summary_adoption_for_uncertified_critic(
                    query,
                    research_plan,
                    sources,
                    claims,
                    stage_timings,
                )
                if adopted:
                    warnings.append("证据裁判未返回有效决策，已按低风险摘要证据合同有限采用直接匹配来源。")
                    return adopted, rejected
                warnings.append("证据裁判未返回有效决策，已拒绝采用未经裁判的网页证据。")
                return [], [_source_rejected_by_contract(source, "evidence_critic_empty") for source in sources]

    adopted, rejected = _apply_evidence_decisions_to_sources(research_plan, sources, decisions)
    adopted, rejected = _repair_insufficient_adoption_with_critic(
        query,
        research_plan,
        sources,
        adopted,
        rejected,
        adapter,
        claims,
        warnings,
        stage_timings=stage_timings,
    )
    adopted, rejected = _repair_missing_claim_coverage_with_critic(
        query,
        research_plan,
        sources,
        adopted,
        rejected,
        adapter,
        claims,
        warnings,
        stage_timings=stage_timings,
    )
    if (
        not adopted
        and _should_retry_broad_low_risk_claim(research_plan, claims)
        and _research_budget_available(EVIDENCE_RETRY_MIN_REMAINING_SECONDS)
    ):
        broad_retry_started = time.monotonic()
        try:
            retry_decisions = _retry_evidence_critic_minimal(
                query,
                research_plan,
                sources,
                ["answer_user_question"],
                adapter,
                stage_timings=stage_timings,
                timing_key=None,
            )
            if not retry_decisions:
                retry_raw = adapter(_evidence_critic_payload(query, research_plan, sources, ["answer_user_question"]))
                retry_decisions = _parse_evidence_critic_decisions(retry_raw)
        except Exception:
            retry_decisions = []
        finally:
            _add_stage_timing(stage_timings, "broad_retry_ms", broad_retry_started)
        if retry_decisions:
            retry_adopted, retry_rejected = _apply_evidence_decisions_to_sources(research_plan, sources, retry_decisions)
            if retry_adopted:
                adopted, rejected = retry_adopted, retry_rejected
                warnings.append("首轮证据裁判未找到可采用来源，已改用总 claim 轻量复核。")
    elif not adopted and _should_retry_broad_low_risk_claim(research_plan, claims):
        warnings.append("研究预算不足，已跳过证据裁判后的总 claim 复核。")
    if _should_prune_redundant_same_domain_adopted_sources(research_plan):
        adopted, redundant_rejected = _prune_redundant_same_domain_adopted_sources(adopted)
        rejected = _dedupe_sources_by_url([*rejected, *redundant_rejected])
    if rejected:
        warnings.append("部分联网来源未通过证据裁判，已从可引用依据中移除。")
    return adopted, rejected


def _timed_limited_summary_adoption_for_uncertified_critic(
    query: str,
    research_plan: dict[str, Any],
    sources: list[dict[str, Any]],
    claims: list[str],
    stage_timings: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    started = time.monotonic()
    try:
        return _limited_summary_adoption_for_uncertified_critic(query, research_plan, sources, claims)
    finally:
        _add_stage_timing(stage_timings, "limited_summary_adoption_ms", started)


def _limited_summary_adoption_for_uncertified_critic(
    query: str,
    research_plan: dict[str, Any],
    sources: list[dict[str, Any]],
    claims: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not _allows_limited_summary_adoption(query, research_plan, claims):
        return [], []
    decisions = [
        decision
        for source in sources
        if (decision := _limited_summary_decision_for_source(query, research_plan, source, claims))
    ]
    if not decisions:
        return [], []
    adopted, rejected = _apply_evidence_decisions_to_sources(research_plan, sources, decisions)
    if not adopted:
        return [], []
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    limit = min(max(int(contract.get("minimum_independent_sources") or 1), 1), 3)
    adopted = adopted[:limit]
    adopted_urls = {str(source.get("url") or "") for source in adopted if source.get("url")}
    adopted = [
        {
            **source,
            "quality_reason": "limited_summary_after_uncertified_critic",
            "evidence_critic_reason": source.get("evidence_critic_reason")
            or "limited low-risk summary adoption after empty evidence critic",
        }
        for source in adopted
    ]
    rejected = [
        source
        for source in rejected
        if str(source.get("url") or "") not in adopted_urls
    ]
    return adopted, rejected


def _allows_limited_summary_adoption(query: str, research_plan: dict[str, Any], claims: list[str]) -> bool:
    if research_plan.get("mode") != "web_only":
        return False
    if not _allows_failed_extract_summary_fallback(research_plan):
        return False
    subject = research_plan.get("subject") if isinstance(research_plan.get("subject"), dict) else {}
    type_hint = str(subject.get("type_hint") or "").strip().lower()
    if type_hint in {
        "current_role",
        "metric",
        "market_metric",
        "finance_term",
        "financial_term",
        "medical_term",
        "legal_term",
        "academic_term",
    }:
        return False
    normalized_claims = _normalized_evidence_claim_ids(_string_list(claims)[:8])
    if not normalized_claims:
        normalized_claims = ["answer_user_question"]
    if not all(_claim_is_stable_overview_claim(claim) for claim in normalized_claims):
        return False
    intent = str(research_plan.get("intent_label") or "").lower()
    fallback_explainer = bool(
        research_plan.get("plan_status") == "fallback"
        and subject.get("name")
        and ENTITY_EXPLAINER_RE.search(query or "")
        and not re.search(
            r"现任|当前|最新|今天|现在|主席|总统|CEO|首席执行官|负责人|多少|价格|市值|排名|指标|current|latest|today|chair|president|price|market\s*cap|rank|metric",
            query or "",
            re.I,
        )
    )
    return bool(
        _plan_is_definition_like(research_plan)
        or type_hint in {"company", "organization", "person", "concept", "term", "topic", "entity"}
        or any(marker in intent for marker in ("overview", "profile", "background", "definition", "简介", "是什么"))
        or fallback_explainer
    )


def _claim_is_stable_overview_claim(claim: str) -> bool:
    value = str(claim or "").strip().lower()
    if not value:
        return False
    if value == "answer_user_question":
        return True
    unsafe_markers = (
        "current",
        "latest",
        "metric",
        "rank",
        "ranking",
        "price",
        "value",
        "score",
        "factor",
        "chair",
        "contraindication",
        "indication",
        "现任",
        "最新",
        "指标",
        "排名",
        "数值",
    )
    if any(marker in value for marker in unsafe_markers):
        return False
    stable_markers = ("identity", "overview", "definition", "profile", "background", "scope", "简介", "定义")
    return any(marker in value for marker in stable_markers)


def _limited_summary_decision_for_source(
    query: str,
    research_plan: dict[str, Any],
    source: dict[str, Any],
    claims: list[str],
) -> dict[str, Any] | None:
    if not _source_matches_subject(query, source):
        return None
    if _source_title_conflicts_with_subject(source, research_plan):
        return None
    if _source_is_adjacent_for_definition_claim(research_plan, source):
        return None
    tags = set(source.get("quality_tags") or [])
    if {"source_identity_mismatch", "low_quality_domain", "low_quality_format", "weak_secondary_source"} & tags:
        return None
    if _is_low_quality_source(source) or _is_weak_secondary_source(source):
        return None
    evidence_text = " ".join(
        str(source.get(key) or "")
        for key in ("title", "description", "excerpt", "content")
    ).strip()
    if not evidence_text:
        return None
    subject = research_plan.get("subject") if isinstance(research_plan.get("subject"), dict) else {}
    preferences = _sanitize_source_preferences(research_plan.get("source_preferences") or {})
    domain = _domain(source.get("url") or "")
    preferred_domains = {str(item or "").lower().removeprefix("www.") for item in preferences.get("preferred_domains") or []}
    preferred_domain = bool(
        domain
        and any(domain == preferred or domain.endswith(f".{preferred}") for preferred in preferred_domains if preferred)
    )
    title_priority = _subject_title_priority(source, subject)
    preferred_or_trusted = bool(
        preferred_domain
        or {"planner_preferred_domain", "planner_primary_source"} & tags
        or source.get("trusted_source")
    )
    if title_priority < 2 and not preferred_or_trusted:
        return None
    supported_claims = _normalized_evidence_claim_ids(_string_list(claims)[:8]) or ["answer_user_question"]
    source_authority = "official" if preferred_or_trusted else "reputable_secondary"
    return {
        "source_id": source.get("url") or source.get("id") or source.get("ref") or "",
        "subject_match": "yes",
        "source_authority": source_authority,
        "source_role": "supporting_evidence",
        "allowed_usage": ["fact", "background"],
        "confidence": "low",
        "supports_claims": supported_claims,
        "claim_support": [
            {"claim_id": claim, "support": "yes", "reason": "direct low-risk overview evidence"}
            for claim in supported_claims
        ],
        "adoption": "partial",
        "reason": "limited low-risk summary adoption after empty evidence critic",
    }


def _repair_insufficient_adoption_with_critic(
    query: str,
    research_plan: dict[str, Any],
    sources: list[dict[str, Any]],
    adopted: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    adapter: ResearchEvidenceCriticAdapter,
    claims: list[str],
    warnings: list[str],
    *,
    stage_timings: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not adopted or not _missing_minimum_reliable_sources(research_plan, adopted):
        return adopted, rejected
    if not _research_budget_available(3.0):
        warnings.append("研究预算不足，已跳过来源不足后的二次证据修复。")
        return adopted, rejected
    if not _has_unadopted_independent_candidates(sources, adopted):
        return adopted, rejected
    repair_candidates = _unadopted_independent_candidates(sources, adopted)
    repair_context = {
        "minimum_independent_sources": _minimum_reliable_source_count(research_plan.get("evidence_contract") or {}),
        "current_adopted_sources": [
            {
                "source_id": source.get("url") or source.get("id") or source.get("ref"),
                "title": source.get("title") or "",
                "domain": source.get("domain") or _domain(source.get("url") or ""),
                "supports_claims": source.get("supports_claims") or [],
            }
            for source in adopted
        ],
        "instruction": "Re-evaluate non-adopted independent candidates. Adopt additional sources only if they directly support the required claims.",
    }
    repair_decisions: list[dict[str, Any]] = []
    repair_started = time.monotonic()
    if _prefer_minimal_evidence_critic(research_plan, claims):
        repair_batches = [repair_candidates[index : index + 2] for index in range(0, len(repair_candidates), 2)]
        repair_decisions = _repair_evidence_critic_minimal_batches(
            query,
            research_plan,
            repair_batches,
            claims,
            adapter,
            repair_context,
            stage_timings=stage_timings,
            timing_key=None,
        )
        if repair_decisions:
            warnings.append("采用来源未达到最低独立来源合同，已用轻量分批裁判复核剩余候选。")
    else:
        payload = _evidence_critic_payload(query, research_plan, repair_candidates, claims)
        payload["minimal_critic"] = True
        payload["minimum_source_repair"] = repair_context
        try:
            repair_raw = adapter(payload)
            repair_decisions = _parse_evidence_critic_decisions(repair_raw)
        except Exception as exc:
            _add_stage_timing(stage_timings, "repair_critic_ms", repair_started)
            warnings.append(f"补充证据裁判失败，已保留首轮采用来源：{exc}")
            return adopted, rejected
    _add_stage_timing(stage_timings, "repair_critic_ms", repair_started)
    if not repair_decisions:
        return adopted, rejected
    repair_adopted, repair_rejected = _apply_evidence_decisions_to_sources(research_plan, repair_candidates, repair_decisions)
    merged = _merge_adopted_sources(adopted, repair_adopted)
    if _independent_source_count(merged) <= _independent_source_count(adopted):
        return adopted, rejected
    adopted_urls = {str(source.get("url") or "") for source in merged if source.get("url")}
    merged_rejected = [
        source
        for source in [*rejected, *repair_rejected]
        if str(source.get("url") or "") not in adopted_urls
    ]
    warnings.append("采用来源未达到最低独立来源合同，已让证据裁判复核剩余候选并补充采用来源。")
    return merged, _dedupe_sources_by_url(merged_rejected)


def _repair_missing_claim_coverage_with_critic(
    query: str,
    research_plan: dict[str, Any],
    sources: list[dict[str, Any]],
    adopted: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    adapter: ResearchEvidenceCriticAdapter,
    claims: list[str],
    warnings: list[str],
    *,
    stage_timings: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    missing_claims = _missing_supported_claims(claims, adopted)
    if not adopted or not missing_claims or not _research_budget_available(3.0):
        return adopted, rejected
    repair_candidates = _unadopted_candidates(sources, adopted)
    if not repair_candidates:
        return adopted, rejected
    repair_context = {
        "missing_claims": missing_claims,
        "current_adopted_sources": [
            {
                "source_id": source.get("url") or source.get("id") or source.get("ref"),
                "title": source.get("title") or "",
                "domain": source.get("domain") or _domain(source.get("url") or ""),
                "supports_claims": source.get("supports_claims") or [],
            }
            for source in adopted
        ],
        "instruction": "Re-evaluate non-adopted candidates for the missing claims. Adopt a candidate only if it directly supports at least one missing claim.",
    }
    repair_decisions: list[dict[str, Any]] = []
    repair_started = time.monotonic()
    if _prefer_minimal_evidence_critic(research_plan, missing_claims):
        repair_batches = [repair_candidates[index : index + 2] for index in range(0, len(repair_candidates), 2)]
        repair_decisions = _repair_evidence_critic_minimal_batches(
            query,
            research_plan,
            repair_batches,
            missing_claims,
            adapter,
            repair_context,
            stage_timings=stage_timings,
            timing_key=None,
        )
    else:
        payload = _evidence_critic_payload(query, research_plan, repair_candidates, missing_claims)
        payload["minimal_critic"] = True
        payload["minimum_source_repair"] = repair_context
        try:
            repair_raw = adapter(payload)
            repair_decisions = _parse_evidence_critic_decisions(repair_raw)
        except Exception as exc:
            _add_stage_timing(stage_timings, "repair_critic_ms", repair_started)
            warnings.append(f"缺失 claim 覆盖复核失败，已保留首轮采用来源：{exc}")
            return adopted, rejected
    _add_stage_timing(stage_timings, "repair_critic_ms", repair_started)
    if not repair_decisions:
        return adopted, rejected
    repair_adopted, repair_rejected = _apply_evidence_decisions_to_sources(research_plan, repair_candidates, repair_decisions)
    merged = _merge_adopted_sources(adopted, repair_adopted)
    before_supported = set(_supported_claims(adopted))
    after_supported = set(_supported_claims(merged))
    if not after_supported - before_supported:
        return adopted, rejected
    adopted_urls = {str(source.get("url") or "") for source in merged if source.get("url")}
    merged_rejected = [
        source
        for source in [*rejected, *repair_rejected]
        if str(source.get("url") or "") not in adopted_urls
    ]
    warnings.append("采用来源未覆盖全部所需 claim，已让证据裁判复核剩余候选并补充采用来源。")
    return merged, _dedupe_sources_by_url(merged_rejected)


def _missing_supported_claims(claims: list[str], adopted: list[dict[str, Any]]) -> list[str]:
    if not claims:
        return []
    supported = set(_supported_claims(adopted))
    return [claim for claim in claims if claim not in supported]


def _supported_claims(sources: list[dict[str, Any]]) -> list[str]:
    claims: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        claims.extend(str(item or "") for item in source.get("supports_claims") or [] if str(item or ""))
    return _unique_queries(claims)


def _unadopted_candidates(
    sources: list[dict[str, Any]],
    adopted: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    adopted_urls = {str(source.get("url") or "") for source in adopted if isinstance(source, dict)}
    return [
        source
        for source in sources
        if isinstance(source, dict) and str(source.get("url") or "") not in adopted_urls
    ]


def _prune_redundant_same_domain_adopted_sources(
    adopted: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    claims_by_domain: dict[str, set[str]] = {}
    for source in adopted:
        domain = str(source.get("domain") or _domain(source.get("url") or ""))
        claims = {str(item or "") for item in source.get("supports_claims") or [] if str(item or "")}
        if domain and claims and claims <= claims_by_domain.get(domain, set()):
            rejected.append(_source_rejected_by_contract(source, "redundant_same_domain_evidence"))
            continue
        kept.append(source)
        if domain:
            claims_by_domain.setdefault(domain, set()).update(claims)
    return kept, rejected


def _should_prune_redundant_same_domain_adopted_sources(research_plan: dict[str, Any]) -> bool:
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    return bool(
        contract.get("prefer_authoritative_sources")
        and int(contract.get("minimum_independent_sources") or 1) > 1
    )


def _unadopted_independent_candidates(
    sources: list[dict[str, Any]],
    adopted: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    adopted_domains = {
        str(source.get("domain") or _domain(source.get("url") or ""))
        for source in adopted
        if isinstance(source, dict)
    }
    adopted_urls = {str(source.get("url") or "") for source in adopted if isinstance(source, dict)}
    candidates = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        if str(source.get("url") or "") in adopted_urls:
            continue
        domain = str(source.get("domain") or _domain(source.get("url") or ""))
        if domain and domain in adopted_domains:
            continue
        candidates.append(source)
    return candidates


def _has_unadopted_independent_candidates(
    sources: list[dict[str, Any]],
    adopted: list[dict[str, Any]],
) -> bool:
    adopted_domains = {
        str(source.get("domain") or _domain(source.get("url") or ""))
        for source in adopted
        if isinstance(source, dict)
    }
    adopted_urls = {str(source.get("url") or "") for source in adopted if isinstance(source, dict)}
    for source in sources:
        if not isinstance(source, dict):
            continue
        if str(source.get("url") or "") in adopted_urls:
            continue
        domain = str(source.get("domain") or _domain(source.get("url") or ""))
        if domain and domain not in adopted_domains:
            return True
    return False


def _merge_adopted_sources(
    current: list[dict[str, Any]],
    additional: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = list(current)
    seen = {str(source.get("url") or source.get("id") or source.get("ref") or "") for source in merged}
    for source in additional:
        key = str(source.get("url") or source.get("id") or source.get("ref") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(source)
    return merged


def _dedupe_sources_by_url(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        key = str(source.get("url") or source.get("id") or source.get("ref") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(source)
    return out


def _apply_evidence_decisions_to_sources(
    research_plan: dict[str, Any],
    sources: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decision_by_id: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        source_id = str(decision.get("source_id") or "").strip()
        if source_id:
            decision_by_id[source_id] = decision

    adopted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    claim_requirements = _sanitize_claim_requirements(research_plan.get("claim_requirements"))
    planned_claims = _normalized_evidence_claim_ids(_string_list(research_plan.get("answer_claims_needed"))[:8])
    for source in sources:
        decision = _decision_for_source(source, decision_by_id)
        if _summary_only_disallowed(source, contract, research_plan):
            rejected.append(_source_rejected_by_contract(source, "summary_only_disallowed"))
        elif decision:
            decision = _apply_claim_requirements_to_decision(decision, claim_requirements, planned_claims, source=source)
            if _source_quality_disallowed_by_contract(source, decision, research_plan, contract):
                rejected.append(_source_rejected_by_quality_contract(source, decision))
            elif _authority_disallowed_by_contract(decision, contract):
                rejected.append(_source_rejected_by_authority_contract(source, decision))
            elif _evidence_decision_adopts(decision):
                adopted_source = _source_with_evidence_decision(source, decision, adopted=True)
                if _source_is_stale_for_freshness_sensitive_plan(research_plan, adopted_source):
                    rejected.append(_source_rejected_by_contract(adopted_source, "stale_for_freshness_sensitive_claim"))
                else:
                    adopted.append(adopted_source)
            else:
                rejected.append(_source_with_evidence_decision(source, decision or {}, adopted=False))
        else:
            rejected.append(_source_with_evidence_decision(source, {}, adopted=False))
    return adopted, rejected
