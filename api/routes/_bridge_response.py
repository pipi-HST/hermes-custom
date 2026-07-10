"""Translate bridge result envelopes into HTTP responses."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi.responses import JSONResponse


_NOT_FOUND_MARKERS = ("not found", "不存在", "未找到", "没有找到")
_CONFLICT_MARKERS = ("不能重复", "不能继续", "已保存", "已入库", "已删除", "已是")


def bridge_result_response(result: Any) -> Any:
    """Keep successful bridge results unchanged and map error envelopes to 4xx."""
    if not isinstance(result, Mapping) or result.get("status") != "error":
        return result

    errors = result.get("errors")
    messages = errors if isinstance(errors, list) else []
    error_text = " ".join(str(message) for message in messages)
    normalized = error_text.lower()
    if any(marker in normalized for marker in _NOT_FOUND_MARKERS):
        status_code = 404
    elif any(marker in normalized for marker in _CONFLICT_MARKERS):
        status_code = 409
    else:
        status_code = 400
    return JSONResponse(status_code=status_code, content=dict(result))
