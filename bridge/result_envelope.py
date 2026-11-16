"""Shared Agent Result Envelope helpers for Hermes bridges."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

ENVELOPE_FIELDS = (
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
)

STATE_CHANGING_INTENTS = {
    "commit_capture_draft",
    "discard_capture_draft",
    "discard_reading_artifact",
    "create_theme_page",
    "create_garden_card",
    "commit_garden_draft",
    "accept_garden_card",
    "archive_garden_card",
    "apply_librarian_repair",
    "mark_review_item_mastered",
    "commit_reading_artifact",
}

_SOURCE_REF_MISSING = object()


def success_envelope(
    *,
    agent: str,
    intent: str,
    chat_preview: str,
    durable_artifacts: list[dict[str, Any]] | None = None,
    source_map: list[dict[str, Any]] | None = None,
    next_actions: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a successful Agent Result Envelope."""
    return _envelope(
        status="success",
        agent=agent,
        intent=intent,
        chat_preview=chat_preview,
        durable_artifacts=durable_artifacts,
        source_map=source_map,
        next_actions=next_actions,
        warnings=warnings,
        errors=[],
        meta=meta,
    )


def error_envelope(
    *,
    agent: str,
    intent: str,
    error: str,
    chat_preview: str | None = None,
    warnings: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an error Agent Result Envelope."""
    preview = chat_preview or error
    return _envelope(
        status="error",
        agent=agent,
        intent=intent,
        chat_preview=preview,
        durable_artifacts=[],
        source_map=[],
        next_actions=[],
        warnings=warnings,
        errors=[error],
        meta=meta,
    )


def artifact_ref(
    *,
    type: str,
    id: str,
    title: str,
    path: str = "",
    status: str = "",
) -> dict[str, str]:
    """Build a Durable Artifact Reference."""
    return {
        "type": type,
        "id": id,
        "title": title,
        "path": path,
        "status": status,
    }


def source_ref(
    *,
    role: str,
    type: str,
    id: str,
    title: str,
    path: str = "",
    section: str = "",
    status: str = "",
    ref: Any = _SOURCE_REF_MISSING,
    file_path: Any = _SOURCE_REF_MISSING,
    excerpt: Any = _SOURCE_REF_MISSING,
    url: Any = _SOURCE_REF_MISSING,
    domain: Any = _SOURCE_REF_MISSING,
    source_kind: Any = _SOURCE_REF_MISSING,
    reading_session_id: Any = _SOURCE_REF_MISSING,
    reading_draft_id: Any = _SOURCE_REF_MISSING,
    score: Any = _SOURCE_REF_MISSING,
    source_count: Any = _SOURCE_REF_MISSING,
    citation_count: Any = _SOURCE_REF_MISSING,
) -> dict[str, str]:
    """Build a Source Reference for a Source Map."""
    item = {
        "role": role,
        "type": type,
        "id": id,
        "title": title,
        "path": path,
        "section": section,
        "status": status,
    }
    optional = {
        "ref": ref,
        "file_path": file_path,
        "excerpt": excerpt,
        "url": url,
        "domain": domain,
        "source_kind": source_kind,
        "reading_session_id": reading_session_id,
        "reading_draft_id": reading_draft_id,
        "score": score,
        "source_count": source_count,
        "citation_count": citation_count,
    }
    item.update({key: value for key, value in optional.items() if value is not _SOURCE_REF_MISSING})
    return item


def next_action(
    *,
    label: str,
    intent: str,
    payload: Mapping[str, Any] | None = None,
    requires_confirmation: bool = False,
) -> dict[str, Any]:
    """Build a structured Next Action."""
    if intent in STATE_CHANGING_INTENTS and not requires_confirmation:
        raise ValueError(f"{intent} requires_confirmation must be true")
    return {
        "label": label,
        "intent": intent,
        "payload": dict(payload or {}),
        "requires_confirmation": requires_confirmation,
    }


def require_envelope(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a plain dict only if value matches the stable envelope shape."""
    if not isinstance(value, Mapping):
        raise TypeError("Agent Result Envelope must be a mapping")
    keys = set(value.keys())
    expected = set(ENVELOPE_FIELDS)
    missing = expected - keys
    extra = keys - expected
    if missing or extra:
        problems = []
        if missing:
            problems.append(f"missing fields: {sorted(missing)}")
        if extra:
            problems.append(f"extra fields: {sorted(extra)}")
        raise ValueError("; ".join(problems))
    return {key: value[key] for key in ENVELOPE_FIELDS}


def envelope_json(value: Mapping[str, Any]) -> str:
    """Serialize an envelope for Hermes tool-result messages."""
    return json.dumps(require_envelope(value), ensure_ascii=False)


def _envelope(
    *,
    status: str,
    agent: str,
    intent: str,
    chat_preview: str,
    durable_artifacts: list[dict[str, Any]] | None,
    source_map: list[dict[str, Any]] | None,
    next_actions: list[dict[str, Any]] | None,
    warnings: list[str] | None,
    errors: list[str] | None,
    meta: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "status": status,
        "agent": agent,
        "intent": intent,
        "chat_preview": chat_preview,
        "durable_artifacts": durable_artifacts or [],
        "source_map": source_map or [],
        "next_actions": next_actions or [],
        "warnings": warnings or [],
        "errors": errors or [],
        "meta": meta or {},
    }
