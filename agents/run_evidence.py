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
            "object_refs": [],
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
        output_ref["answer"] = meta.get("answer", "")
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
    elif intent in {
        "create_capture_draft",
        "show_capture_draft",
        "revise_capture_draft",
        "commit_capture_draft",
        "refine_capture",
    }:
        draft_artifact = _first_artifact(artifacts, "capture_draft")
        capture_artifact = _first_artifact(artifacts, "capture_note")
        draft_id = str(meta.get("draft_id") or draft_artifact.get("id") or "")
        capture_id = str(meta.get("capture_id") or capture_artifact.get("id") or "")
        evidence.update(
            {
                "draft_id": draft_id,
                "capture_id": capture_id,
                "draft_status": str(meta.get("draft_status") or draft_artifact.get("status") or ""),
                "title": str(meta.get("title") or draft_artifact.get("title") or capture_artifact.get("title") or ""),
                "preview_truncated": bool(meta.get("preview_truncated")),
            }
        )
        output_ref.update(
            {
                "draft_id": draft_id,
                "capture_id": capture_id,
                "title": evidence["title"],
                "draft_status": evidence["draft_status"],
                "file_path": str(meta.get("file_path") or capture_artifact.get("path") or ""),
            }
        )
    elif intent in {
        "start_reading_session",
        "discuss_reading",
        "show_reading_session",
        "summarize_reading_session",
    }:
        session_artifact = _first_artifact(artifacts, "reading_session")
        draft_artifact = _first_artifact(artifacts, "reading_draft")
        session_id = str(
            meta.get("reading_session_id") or session_artifact.get("id") or ""
        )
        draft_id = str(
            meta.get("reading_draft_id") or draft_artifact.get("id") or ""
        )
        evidence.update(
            {
                "reading_session_id": session_id,
                "reading_draft_id": draft_id,
                "source_type": str(meta.get("source_type") or ""),
                "output_type": str(meta.get("output_type") or ""),
                "turn_index": int(meta.get("turn_index") or 0),
                "turn_count": int(meta.get("turn_count") or 0),
                "evidence_count": int(meta.get("evidence_count") or 0),
                "needs_clarification": bool(meta.get("needs_clarification")),
            }
        )
        output_ref.update(
            {
                "reading_session_id": session_id,
                "reading_draft_id": draft_id,
                "source_title": str(
                    meta.get("source_title") or session_artifact.get("title") or ""
                ),
            }
        )
    elif intent in {"save_answer_to_knowledge", "revise_answer_draft", "commit_reading_artifact"}:
        draft_artifact = _first_artifact(artifacts, "reading_draft")
        reading_artifact = (
            _first_artifact(artifacts, "learning_note")
            or _first_artifact(artifacts, "discussion_note")
            or _first_artifact(artifacts, "knowledge_card")
            or _first_artifact(artifacts, "capture_note")
        )
        draft_id = str(meta.get("reading_draft_id") or draft_artifact.get("id") or "")
        artifact_id = str(
            meta.get("capture_id")
            or meta.get("garden_card_id")
            or reading_artifact.get("id")
            or ""
        )
        path = str(meta.get("file_path") or reading_artifact.get("path") or "")
        evidence.update(
            {
                "draft_id": draft_id,
                "artifact_id": artifact_id,
                "artifact_type": str(meta.get("artifact_type") or reading_artifact.get("type") or ""),
                "path": path,
                "answer_source": str(meta.get("answer_source") or ""),
            }
        )
        output_ref.update(
            {
                "draft_id": draft_id,
                "artifact_id": artifact_id,
                "artifact_type": evidence["artifact_type"],
                "path": path,
            }
        )

    status = _completion_status(result, intent, evidence)
    return {
        "status": status,
        "evidence": evidence,
        "output_ref": output_ref,
        "object_refs": _object_refs(intent, artifacts, sources, meta, output_ref, evidence),
        "warnings": warnings,
        "errors": errors,
    }


def extract_input_object_refs(
    intent: str,
    input_ref: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    if not isinstance(input_ref, Mapping):
        return []
    refs: list[dict[str, str]] = []
    if intent in {"synthesize_garden", "run_theme_scan", "search_knowledge_base"}:
        for capture_id in _strings(input_ref.get("capture_ids")):
            _add_ref(refs, "capture", capture_id, role="source", source="input.capture_ids")
    for key, object_type in (
        ("capture_id", "capture"),
        ("draft_id", "capture_draft"),
        ("reading_draft_id", "reading_draft"),
        ("garden_draft_id", "garden_draft"),
        ("card_id", "garden_card"),
        ("garden_card_id", "garden_card"),
        ("review_item_id", "review_item"),
        ("question_id", "pending_question"),
    ):
        _add_ref(refs, object_type, input_ref.get(key), role="subject", source=f"input.{key}")
    return _dedupe_refs(refs)


def _object_refs(
    intent: str,
    artifacts: list[Any],
    sources: list[Any],
    meta: Mapping[str, Any],
    output_ref: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []

    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            continue
        object_type = _object_type_for_artifact(str(artifact.get("type") or ""))
        if object_type:
            _add_ref(
                refs,
                object_type,
                artifact.get("id"),
                role="subject",
                source="durable_artifacts",
            )

    for source in sources:
        if not isinstance(source, Mapping):
            continue
        object_type = _object_type_for_artifact(str(source.get("type") or ""))
        if object_type:
            _add_ref(refs, object_type, source.get("id"), role="source", source="source_map")

    if intent in {"synthesize_garden", "run_theme_scan"}:
        for capture_id in _strings(meta.get("capture_ids")):
            _add_ref(refs, "capture", capture_id, role="source", source="meta.capture_ids")
    if intent in {"create_capture_draft", "show_capture_draft", "revise_capture_draft", "commit_capture_draft", "refine_capture"}:
        _add_ref(refs, "capture_draft", evidence.get("draft_id"), role="subject", source="evidence.draft_id")
        _add_ref(refs, "capture", evidence.get("capture_id"), role="subject", source="evidence.capture_id")
    if intent in {"start_reading_session", "discuss_reading", "show_reading_session", "summarize_reading_session"}:
        _add_ref(refs, "reading_draft", evidence.get("reading_draft_id"), role="subject", source="evidence.reading_draft_id")
    if intent in {"save_answer_to_knowledge", "revise_answer_draft", "commit_reading_artifact"}:
        _add_ref(refs, "reading_draft", evidence.get("draft_id"), role="subject", source="evidence.draft_id")
        artifact_type = str(evidence.get("artifact_type") or "")
        artifact_object_type = _object_type_for_artifact(artifact_type)
        if artifact_object_type:
            _add_ref(refs, artifact_object_type, evidence.get("artifact_id"), role="subject", source="evidence.artifact_id")
    if intent in {"synthesize_garden", "revise_garden_draft"}:
        _add_ref(refs, "garden_draft", output_ref.get("draft_id"), role="subject", source="output.draft_id")
    if intent == "commit_garden_draft":
        _add_ref(refs, "garden_card", output_ref.get("card_id"), role="subject", source="output.card_id")
    if intent == "run_daily_review":
        _add_ref(refs, "review_item", evidence.get("review_item_id"), role="subject", source="evidence.review_item_id")
        for review_item_id in _strings(meta.get("review_item_ids")):
            _add_ref(refs, "review_item", review_item_id, role="subject", source="meta.review_item_ids")
    if intent == "run_theme_scan":
        for question_id in _strings(meta.get("question_ids")):
            _add_ref(refs, "pending_question", question_id, role="subject", source="meta.question_ids")
        for theme_page_id in _strings(meta.get("theme_page_ids")):
            _add_ref(refs, "theme_page", theme_page_id, role="subject", source="meta.theme_page_ids")
    if meta.get("question_id"):
        _add_ref(refs, "pending_question", meta.get("question_id"), role="source", source="meta.question_id")

    return _dedupe_refs(refs)


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
    if intent == "start_reading_session" and not evidence.get("reading_session_id"):
        return "incomplete"
    if (
        intent == "summarize_reading_session"
        and not evidence.get("reading_draft_id")
        and not evidence.get("needs_clarification")
    ):
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


def _object_type_for_artifact(artifact_type: str) -> str:
    return {
        "capture": "capture",
        "capture_note": "capture",
        "capture_section": "capture",
        "learning_note": "capture",
        "discussion_note": "capture",
        "capture_draft": "capture_draft",
        "reading_draft": "reading_draft",
        "garden_draft": "garden_draft",
        "garden_card": "garden_card",
        "knowledge_card": "garden_card",
        "review_item": "review_item",
        "pending_question": "pending_question",
        "theme_page": "theme_page",
    }.get(artifact_type, "")


def _add_ref(
    refs: list[dict[str, str]],
    object_type: str,
    object_id: Any,
    *,
    role: str,
    source: str,
) -> None:
    object_type = str(object_type or "").strip()
    object_id = str(object_id or "").strip()
    if not object_type or not object_id:
        return
    refs.append(
        {
            "object_type": object_type,
            "object_id": object_id,
            "role": str(role or "subject").strip() or "subject",
            "source": str(source or "").strip(),
        }
    )


def _dedupe_refs(refs: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, str]] = []
    for ref in refs:
        key = (ref["object_type"], ref["object_id"], ref["role"])
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def _strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
