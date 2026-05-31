"""Extract machine-checkable completion evidence from Agent Result Envelopes."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def extract_run_evidence(
    agent: str,
    intent: str,
    result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return ledger-ready status, evidence, output refs, warnings, and errors."""
    if not isinstance(result, Mapping):
        return {
            "status": "error",
            "evidence": {"has_envelope": False},
            "output_ref": {},
            "warnings": [],
            "errors": ["tool did not return an envelope"],
        }

    artifacts = _list(result.get("durable_artifacts"))
    sources = _list(result.get("source_map"))
    warnings = [str(item) for item in _list(result.get("warnings"))]
    errors = [str(item) for item in _list(result.get("errors"))]
    meta = result.get("meta") if isinstance(result.get("meta"), Mapping) else {}

    evidence: dict[str, Any] = {
        "has_envelope": _has_envelope_shape(result),
        "status": result.get("status", ""),
        "artifact_count": len(artifacts),
        "source_count": len(sources),
        "warning_count": len(warnings),
        "error_count": len(errors),
    }
    output_ref: dict[str, Any] = {}

    if intent == "search_knowledge_base":
        source_refs = _source_refs(sources)
        graph_trace = meta.get("graph_trace") if isinstance(meta.get("graph_trace"), Mapping) else {}
        evidence.update(
            {
                "result_count": int(meta.get("result_count") or 0),
                "used_index": bool(meta.get("used_index")),
                "used_fallback": bool(meta.get("used_fallback")),
                "source_count": len(sources),
                "source_titles": [
                    source["title"] for source in source_refs if source["title"]
                ],
            }
        )
        if graph_trace:
            evidence.update(
                {
                    "graph_seed_count": int(graph_trace.get("seed_count") or 0),
                    "graph_garden_count": int(graph_trace.get("garden_count") or 0),
                    "graph_theme_count": int(graph_trace.get("theme_count") or 0),
                    "graph_final_count": int(graph_trace.get("final_count") or 0),
                    "graph_stopped_reason": str(graph_trace.get("stopped_reason") or ""),
                }
            )
        output_ref["query"] = meta.get("query", "")
        output_ref["sources"] = source_refs
    elif intent == "synthesize_garden":
        draft_artifact = _first_artifact(artifacts, "garden_draft")
        draft_id = str(meta.get("draft_id") or draft_artifact.get("id") or "")
        capture_ids = meta.get("capture_ids") if isinstance(meta.get("capture_ids"), list) else []
        evidence.update(
            {
                "draft_created": bool(draft_id),
                "draft_id": draft_id,
                "capture_count": len(capture_ids),
                "knowledge_link_count": int(meta.get("knowledge_link_count") or 0),
            }
        )
        output_ref["draft_id"] = draft_id
    elif intent == "revise_garden_draft":
        draft_id = str(meta.get("draft_id") or "")
        evidence.update({"draft_updated": bool(draft_id), "draft_id": draft_id})
        output_ref["draft_id"] = draft_id
    elif intent == "commit_garden_draft":
        card_artifact = _first_artifact(artifacts, "garden_card")
        card_id = str(meta.get("card_id") or card_artifact.get("id") or "")
        path = str(meta.get("path") or card_artifact.get("path") or "")
        evidence.update(
            {
                "card_created": bool(card_id and path),
                "card_id": card_id,
                "path": path,
                "knowledge_link_count": int(meta.get("knowledge_link_count") or 0),
            }
        )
        output_ref.update({"card_id": card_id, "path": path})
    elif intent in {"audit_library", "apply_librarian_repair"}:
        evidence.update(
            {
                "database_sync_checked": "database_sync" in meta,
                "outlines_checked": "outlines" in meta,
                "property_gaps_checked": "property_gaps" in meta,
                "embedding_index_checked": "embedding_index" in meta,
                "report_path": meta.get("report_path", ""),
            }
        )
        output_ref["report_path"] = meta.get("report_path", "")
    elif intent == "run_theme_scan":
        evidence.update(
            {
                "scanned_count": int(meta.get("scanned_count") or 0),
                "theme_pages_written": int(meta.get("theme_pages_created") or 0)
                + int(meta.get("theme_pages_updated") or 0),
                "pending_questions_created": int(meta.get("cluster_count") or 0),
            }
        )
    elif intent == "run_daily_review":
        evidence.update(
            {
                "review_item_id": _first(meta.get("review_item_ids")),
                "view_count_incremented": int(meta.get("item_count") or 0) > 0,
            }
        )
    elif intent == "expand_latest_review_followup":
        evidence.update(
            {
                "followup_answered": bool(meta.get("found")),
                "source_count": len(sources),
            }
        )

    status = _completion_status(result, intent, evidence)
    return {
        "status": status,
        "evidence": evidence,
        "output_ref": output_ref,
        "warnings": warnings,
        "errors": errors,
    }


def _completion_status(
    result: Mapping[str, Any],
    intent: str,
    evidence: dict[str, Any],
) -> str:
    if result.get("status") == "error":
        return "error"
    if intent == "synthesize_garden" and not evidence.get("draft_created"):
        return "incomplete"
    if intent == "revise_garden_draft" and not evidence.get("draft_updated"):
        return "incomplete"
    if intent == "commit_garden_draft" and not evidence.get("card_created"):
        return "incomplete"
    return "success"


def _has_envelope_shape(result: Mapping[str, Any]) -> bool:
    required = {
        "status",
        "agent",
        "intent",
        "chat_preview",
        "durable_artifacts",
        "source_map",
        "next_actions",
        "warnings",
        "errors",
        "meta",
    }
    return required <= set(result.keys())


def _first_artifact(artifacts: list[Any], artifact_type: str) -> dict[str, Any]:
    for artifact in artifacts:
        if isinstance(artifact, Mapping) and artifact.get("type") == artifact_type:
            return dict(artifact)
    return {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    return ""


def _source_refs(sources: list[Any]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        refs.append(
            {
                "id": str(source.get("id") or ""),
                "title": str(source.get("title") or ""),
                "path": str(source.get("path") or ""),
                "section": str(source.get("section") or ""),
                "type": str(source.get("type") or ""),
            }
        )
    return refs
