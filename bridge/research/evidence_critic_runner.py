"""Retry and batching strategy for the evidence critic."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from typing import Any

from bridge.research.evidence_critic_io import _evidence_critic_payload, _parse_evidence_critic_decisions
from bridge.research.search import _allows_secondary_evidence_candidates
from bridge.research.timing import _add_stage_timing
from bridge.research.types import ResearchEvidenceCriticAdapter
from bridge.research.utils import (
    _research_budget_available,
    _sanitize_claim_requirements,
    _sanitize_evidence_contract,
    _sanitize_source_preferences,
)


def _retry_evidence_critic_in_batches(
    query: str,
    research_plan: dict[str, Any],
    sources: list[dict[str, Any]],
    claims: list[str],
    adapter: ResearchEvidenceCriticAdapter,
    *,
    stage_timings: dict[str, Any] | None = None,
    timing_key: str | None = "batch_critic_ms",
) -> list[dict[str, Any]]:
    if len(sources) <= 2:
        return []
    started = time.monotonic()
    batches = [sources[index : index + 2] for index in range(0, len(sources), 2)]
    batches = batches[:3]

    def run_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raw = adapter(_evidence_critic_payload(query, research_plan, batch, claims))
        return _parse_evidence_critic_decisions(raw)

    decisions: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(len(batches), 3))) as executor:
        futures = [executor.submit(run_batch, batch) for batch in batches]
        for future in as_completed(futures):
            if not _research_budget_available(1.0):
                break
            try:
                decisions.extend(future.result())
            except Exception:
                continue
    if timing_key:
        _add_stage_timing(stage_timings, timing_key, started)
    return decisions


def _prefer_minimal_evidence_critic(research_plan: dict[str, Any], claims: list[str]) -> bool:
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    preferences = _sanitize_source_preferences(research_plan.get("source_preferences") or {})
    claim_requirements = _sanitize_claim_requirements(research_plan.get("claim_requirements"))
    preferred_kinds = {str(kind).strip().lower() for kind in preferences.get("preferred_source_kinds") or []}
    subject = research_plan.get("subject") if isinstance(research_plan.get("subject"), dict) else {}
    type_hint = str(subject.get("type_hint") or "").lower()
    lightweight_kinds = {"analysis", "commentary", "interpretive", "explanation"}
    simple_low_risk_claim = (
        bool(claims)
        and len(claims) <= 2
        and not claim_requirements
        and type_hint not in {"finance_term", "financial_term", "medical_term", "legal_term", "academic_term"}
    )
    is_lightweight_candidate = bool(
        research_plan.get("answer_depth") == "detailed"
        or lightweight_kinds & preferred_kinds
        or "interpret" in type_hint
        or simple_low_risk_claim
    )
    return (
        research_plan.get("mode") == "web_only"
        and contract.get("allow_summary_only")
        and _allows_secondary_evidence_candidates(research_plan)
        and is_lightweight_candidate
        and bool(claims)
    )


def _retry_evidence_critic_minimal(
    query: str,
    research_plan: dict[str, Any],
    sources: list[dict[str, Any]],
    claims: list[str],
    adapter: ResearchEvidenceCriticAdapter,
    *,
    stage_timings: dict[str, Any] | None = None,
    timing_key: str | None = "minimal_critic_ms",
) -> list[dict[str, Any]]:
    if not _research_budget_available(3.0):
        return []
    started = time.monotonic()
    batch_size = 2 if len(sources) > 1 else 1
    batches = [sources[index : index + batch_size] for index in range(0, len(sources), batch_size)]
    batches = batches[:3]

    def run_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        payload = _evidence_critic_payload(query, research_plan, batch, claims)
        payload["minimal_critic"] = True
        raw = adapter(payload)
        return _parse_evidence_critic_decisions(raw)

    decisions: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(len(batches), 3))) as executor:
        futures = [executor.submit(run_batch, batch) for batch in batches]
        for future in as_completed(futures):
            if not _research_budget_available(1.0):
                break
            try:
                decisions.extend(future.result())
            except Exception:
                continue
    if timing_key:
        _add_stage_timing(stage_timings, timing_key, started)
    return decisions


def _repair_evidence_critic_minimal_batches(
    query: str,
    research_plan: dict[str, Any],
    batches: list[list[dict[str, Any]]],
    claims: list[str],
    adapter: ResearchEvidenceCriticAdapter,
    repair_context: dict[str, Any],
    *,
    stage_timings: dict[str, Any] | None = None,
    timing_key: str | None = None,
) -> list[dict[str, Any]]:
    if not batches or not _research_budget_available(3.0):
        return []
    started = time.monotonic()

    def run_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        payload = _evidence_critic_payload(query, research_plan, batch, claims)
        payload["minimal_critic"] = True
        payload["minimum_source_repair"] = repair_context
        raw = adapter(payload)
        return _parse_evidence_critic_decisions(raw)

    decisions: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(len(batches), 3))) as executor:
        futures = [executor.submit(run_batch, batch) for batch in batches[:3]]
        for future in as_completed(futures):
            if not _research_budget_available(1.0):
                break
            try:
                decisions.extend(future.result())
            except Exception:
                continue
    if timing_key:
        _add_stage_timing(stage_timings, timing_key, started)
    return decisions
