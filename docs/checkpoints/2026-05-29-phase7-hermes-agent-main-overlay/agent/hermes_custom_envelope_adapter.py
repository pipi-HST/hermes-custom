"""Model-facing adapter for Hermes custom Agent Result Envelopes."""

from __future__ import annotations

import json
from typing import Any, Optional

AGENT_RESULT_ENVELOPE_FIELDS = {
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


def agent_result_envelope_content_for_model(result: Any) -> Optional[str]:
    """Return chat-preview-first content for a serialized Agent Result Envelope."""
    if not isinstance(result, str):
        return None
    try:
        envelope = json.loads(result)
    except Exception:
        return None
    if not isinstance(envelope, dict) or set(envelope) != AGENT_RESULT_ENVELOPE_FIELDS:
        return None

    chat_preview = str(envelope.get("chat_preview") or "")
    structured = {
        key: envelope.get(key)
        for key in (
            "status",
            "agent",
            "intent",
            "durable_artifacts",
            "source_map",
            "next_actions",
            "warnings",
            "errors",
            "meta",
        )
    }
    return (
        "USER_VISIBLE_REPLY:\n"
        f"{chat_preview}\n\n"
        "STRUCTURED_AGENT_RESULT_DO_NOT_REWRITE:\n"
        f"{json.dumps(structured, ensure_ascii=False)}\n\n"
        "Use USER_VISIBLE_REPLY as the chat response. Preserve structured result fields exactly."
    )
