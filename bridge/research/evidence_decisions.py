"""Evidence critic decision contract helpers."""
from __future__ import annotations

import re
from typing import Any

from bridge.research.search import (
    _allows_secondary_evidence_candidates,
    _current_year,
    _is_low_quality_source,
    _is_weak_secondary_source,
)
from bridge.research.utils import (
    _claim_requires_observed_metric_value,
    _enum_value,
    _sanitize_claim_requirements,
    _sanitize_evidence_contract,
    _sanitize_source_preferences,
    _source_has_observed_metric_value,
    _string_list,
    _unique_queries,
)


def _should_retry_broad_low_risk_claim(research_plan: dict[str, Any], claims: list[str]) -> bool:
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    claim_requirements = _sanitize_claim_requirements(research_plan.get("claim_requirements"))
    return (
        bool(claims)
        and research_plan.get("mode") == "web_only"
        and research_plan.get("risk_level") != "high"
        and not research_plan.get("freshness_sensitive")
        and contract.get("allow_summary_only")
        and not contract.get("must_use_primary_source")
        and _allows_secondary_evidence_candidates(research_plan)
        and not any(
            requirement.get("required_authority") in {"primary_or_index", "primary_only"}
            for requirement in claim_requirements.values()
        )
    )


def _authority_disallowed_by_contract(decision: dict[str, Any], contract: dict[str, Any]) -> bool:
    if not contract.get("prefer_authoritative_sources"):
        return False
    if decision.get("adoption") not in {"adopt", "partial"}:
        return False
    return str(decision.get("source_authority") or "unknown") in {"weak_secondary", "unknown"}


def _source_quality_disallowed_by_contract(
    source: dict[str, Any],
    decision: dict[str, Any],
    research_plan: dict[str, Any],
    contract: dict[str, Any],
) -> bool:
    if not contract.get("prefer_authoritative_sources"):
        return False
    if decision.get("adoption") not in {"adopt", "partial"}:
        return False
    if (
        (_is_low_quality_source(source) or _is_weak_secondary_source(source))
        and not _plan_allows_interpretive_commentary(research_plan, decision)
    ):
        return True
    if (
        str(decision.get("source_authority") or "") == "commentary_secondary"
        and not _plan_allows_interpretive_commentary(research_plan, decision)
    ):
        return True
    return False


def _plan_allows_interpretive_commentary(research_plan: dict[str, Any], decision: dict[str, Any]) -> bool:
    if research_plan.get("risk_level") == "high" or research_plan.get("freshness_sensitive"):
        return False
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    if contract.get("must_use_primary_source"):
        return False
    claim_requirements = _sanitize_claim_requirements(research_plan.get("claim_requirements"))
    if any(
        requirement.get("required_authority") in {"primary_or_index", "primary_only"}
        for requirement in claim_requirements.values()
    ):
        return False
    if decision.get("evidence_role") != "interpretive_evidence":
        return False
    if "interpretation" not in set(decision.get("allowed_usage") or []):
        return False
    preferences = _sanitize_source_preferences(research_plan.get("source_preferences") or {})
    preferred_kinds = {str(kind).strip().lower() for kind in preferences.get("preferred_source_kinds") or []}
    subject = research_plan.get("subject") if isinstance(research_plan.get("subject"), dict) else {}
    type_hint = str(subject.get("type_hint") or "").lower()
    claims = " ".join(str(item or "").lower() for item in _string_list(research_plan.get("answer_claims_needed")))
    return bool(
        research_plan.get("answer_depth") == "detailed"
        or preferred_kinds & {"analysis", "commentary", "interpretive", "explanation", "film_review"}
        or any(marker in type_hint for marker in ("analysis", "commentary", "interpret", "plot", "drama"))
        or any(marker in claims for marker in ("interpret", "explanation", "why", "mechanism"))
    )


def _source_rejected_by_authority_contract(source: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    item = _source_with_evidence_decision(source, {**decision, "adoption": "reject"}, adopted=False)
    item["quality_reason"] = "source_authority_below_contract"
    item["evidence_critic_reason"] = decision.get("reason") or "source authority did not satisfy evidence contract"
    return item


def _source_rejected_by_quality_contract(source: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    item = _source_with_evidence_decision(source, {**decision, "adoption": "reject"}, adopted=False)
    item["quality_reason"] = "source_quality_below_contract"
    item["evidence_critic_reason"] = decision.get("reason") or "source quality did not satisfy evidence contract"
    return item


def _apply_claim_requirements_to_decision(
    decision: dict[str, Any],
    claim_requirements: dict[str, dict[str, str]],
    allowed_claims: list[str] | None = None,
    *,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not claim_requirements or not decision.get("supports_claims"):
        return decision
    source_authority = str(decision.get("source_authority") or "unknown")
    allowed = set(allowed_claims or [])
    supported: list[str] = []
    unsupported = list(decision.get("unsupported_claims") or [])
    claim_support = []
    for item in decision.get("claim_support") or []:
        claim_id = str(item.get("claim_id") or "")
        if item.get("support") != "yes":
            claim_support.append(item)
            continue
        requirement = claim_requirements.get(claim_id) or {}
        if allowed and claim_id not in allowed:
            unsupported.append(claim_id)
            claim_support.append(
                {
                    **item,
                    "support": "no",
                    "reason": f"claim {claim_id} is outside planned answer claims",
                }
            )
            continue
        if not requirement:
            requirement = {"required_authority": "any_reliable"}
        required_authority = requirement.get("required_authority") or "any_reliable"
        if (
            _authority_satisfies_requirement(source_authority, required_authority)
            and not _strict_metric_claim_lacks_observed_value(source or {}, claim_id, required_authority)
        ):
            supported.append(claim_id)
            claim_support.append(item)
        else:
            unsupported.append(claim_id)
            if _authority_satisfies_requirement(source_authority, required_authority):
                reason = f"source does not expose an observed value for {claim_id}"
            else:
                reason = f"source_authority {source_authority} does not satisfy {required_authority}"
            claim_support.append(
                {
                    **item,
                    "support": "no",
                    "reason": reason,
                }
            )
    updated = dict(decision)
    updated["supports_claims"] = _unique_queries(supported)
    updated["unsupported_claims"] = _unique_queries(unsupported)
    updated["claim_support"] = claim_support
    return updated


def _strict_metric_claim_lacks_observed_value(
    source: dict[str, Any],
    claim_id: str,
    required_authority: str,
) -> bool:
    if required_authority not in {"primary_or_index", "primary_only"}:
        return False
    if not _claim_requires_observed_metric_value(claim_id):
        return False
    return not _source_has_observed_metric_value(source)


def _authority_satisfies_requirement(source_authority: str, required_authority: str) -> bool:
    if required_authority == "any_reliable":
        return source_authority in {"primary", "index_database", "official", "reputable_secondary"}
    if required_authority == "primary_or_index":
        return source_authority in {"primary", "index_database", "official"}
    if required_authority == "primary_only":
        return source_authority == "primary"
    return False


def _decision_for_source(source: dict[str, Any], decisions: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    keys = [
        str(source.get("url") or ""),
        str(source.get("id") or ""),
        str(source.get("ref") or ""),
    ]
    for key in keys:
        if key and key in decisions:
            return decisions[key]
    return None


def _evidence_decision_adopts(decision: dict[str, Any]) -> bool:
    return (
        decision.get("adoption") in {"adopt", "partial"}
        and decision.get("subject_match") == "yes"
        and decision.get("evidence_role") not in {"clue", "reject"}
        and bool(decision.get("supports_claims"))
    )


def _summary_only_disallowed(source: dict[str, Any], contract: dict[str, Any], research_plan: dict[str, Any]) -> bool:
    if contract.get("allow_summary_only") or source.get("extract_status") == "full":
        return False
    if _summary_only_can_enter_critic(source, research_plan):
        return False
    return True


def _summary_only_can_enter_critic(source: dict[str, Any], research_plan: dict[str, Any]) -> bool:
    if research_plan.get("risk_level") == "high" or research_plan.get("freshness_sensitive"):
        return False
    if (research_plan.get("evidence_contract") or {}).get("must_use_primary_source"):
        return False
    if _string_list(research_plan.get("answer_claims_needed")):
        return False
    claim_requirements = _sanitize_claim_requirements(research_plan.get("claim_requirements"))
    if any(
        requirement.get("required_authority") in {"primary_or_index", "primary_only"}
        for requirement in claim_requirements.values()
    ):
        return False
    if source.get("extract_status") != "summary":
        return False
    return bool(source.get("excerpt") or source.get("description"))


def _allows_failed_extract_summary_fallback(research_plan: dict[str, Any]) -> bool:
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    if not contract.get("allow_summary_only"):
        return False
    if research_plan.get("risk_level") == "high" or research_plan.get("freshness_sensitive"):
        return False
    if contract.get("must_use_primary_source"):
        return False
    claim_requirements = _sanitize_claim_requirements(research_plan.get("claim_requirements"))
    if any(
        requirement.get("required_authority") in {"primary_or_index", "primary_only"}
        for requirement in claim_requirements.values()
    ):
        return False
    return True


def _allows_failed_extract_title_fallback(research_plan: dict[str, Any]) -> bool:
    if not _allows_failed_extract_summary_fallback(research_plan):
        return False
    preferences = _sanitize_source_preferences(research_plan.get("source_preferences") or {})
    preferred_kinds = {str(kind).strip().lower() for kind in preferences.get("preferred_source_kinds") or []}
    subject = research_plan.get("subject") if isinstance(research_plan.get("subject"), dict) else {}
    type_hint = str(subject.get("type_hint") or "").lower()
    return bool(
        research_plan.get("answer_depth") == "detailed"
        or {"analysis", "commentary", "interpretive", "explanation", "film_review"} & preferred_kinds
        or any(marker in type_hint for marker in ("analysis", "commentary", "interpret", "plot"))
    )


def _source_is_stale_for_freshness_sensitive_plan(
    research_plan: dict[str, Any],
    source: dict[str, Any],
) -> bool:
    if not research_plan.get("freshness_sensitive"):
        return False
    current_year = _current_year()
    if not current_year:
        return False
    text = " ".join(
        str(source.get(key) or "")
        for key in ("title", "description", "excerpt", "evidence_critic_reason")
    )
    years = {int(match) for match in re.findall(r"\b(20\d{2})\b", text)}
    if not years or current_year in years:
        return False
    return max(years) < current_year


def _source_with_evidence_decision(
    source: dict[str, Any],
    decision: dict[str, Any],
    *,
    adopted: bool,
) -> dict[str, Any]:
    item = dict(source)
    adoption = _enum_value(decision.get("adoption") if decision else "", {"adopt", "partial", "reject", "clue"}, "reject")
    if adopted:
        role = "evidence"
        reason = "evidence_critic_adopted"
    elif adoption == "clue":
        role = "clue"
        reason = "evidence_critic_clue"
    else:
        role = "rejected"
        reason = "evidence_critic_rejected"
    item["source_role"] = role
    item["evidence_role"] = decision.get("evidence_role") or ("supporting_evidence" if adopted else role)
    item["quality_reason"] = reason
    item["entity_match"] = decision.get("subject_match") == "yes" if decision else source.get("entity_match")
    item["source_authority"] = decision.get("source_authority") or "unknown"
    item["allowed_usage"] = decision.get("allowed_usage") or []
    item["confidence"] = decision.get("confidence") or ""
    item["supports_claims"] = decision.get("supports_claims") or []
    item["unsupported_claims"] = decision.get("unsupported_claims") or []
    item["claim_support"] = decision.get("claim_support") or []
    item["evidence_critic_reason"] = decision.get("reason") or "critic did not adopt this source"
    return item


def _source_rejected_by_contract(source: dict[str, Any], reason: str) -> dict[str, Any]:
    item = dict(source)
    item["source_role"] = "rejected"
    item["evidence_role"] = "reject"
    item["quality_reason"] = reason
    item["source_authority"] = "unknown"
    item["allowed_usage"] = []
    item["confidence"] = ""
    item["supports_claims"] = []
    item["evidence_critic_reason"] = "source did not satisfy deterministic evidence contract"
    return item
