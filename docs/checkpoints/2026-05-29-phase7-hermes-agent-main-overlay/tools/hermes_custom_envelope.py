"""Helpers for Hermes custom Agent Result Envelope tools."""

from __future__ import annotations

import sys
from typing import Any

HERMES_CUSTOM = "/Users/pipi/hermes-custom"
if HERMES_CUSTOM not in sys.path:
    sys.path.insert(0, HERMES_CUSTOM)

from bridge.result_envelope import envelope_json, error_envelope


def _sanitize_error(message: str) -> str:
    try:
        from model_tools import _sanitize_tool_error
    except Exception:
        return message
    return _sanitize_tool_error(message)


def envelope_error_json(*, agent: str, intent: str, exc: Exception, meta: dict[str, Any] | None = None) -> str:
    error = _sanitize_error(f"{type(exc).__name__}: {exc}")
    return envelope_json(
        error_envelope(
            agent=agent,
            intent=intent,
            error=error,
            chat_preview=f"工具执行失败：{error}",
            meta={
                "exception_type": type(exc).__name__,
                **(meta or {}),
            },
        )
    )
