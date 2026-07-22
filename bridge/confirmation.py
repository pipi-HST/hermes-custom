"""Deterministic confirmation gates for durable Knowledge mutations."""

from __future__ import annotations

import re


TRUSTED_CONFIRMATION_SOURCES = frozenset({"api", "dashboard", "manual"})

_CONFIRMATION_PHRASES = {
    "commit_capture_draft": frozenset(
        {
            "可以入库",
            "可以存库",
            "入库",
            "存库",
            "就这样",
            "就按这个",
            "保存到知识库",
            "确认保存",
            "确认入库",
            "确认存库",
        }
    ),
    "commit_garden_draft": frozenset(
        {
            "可以入库",
            "卡片入库",
            "就这样",
            "就按这个",
            "存入知识卡片",
            "确认入库",
            "确认卡片",
            "确认卡片入库",
        }
    ),
    "apply_librarian_repair": frozenset(
        {
            "可以修复",
            "修复吧",
            "应用修复",
            "执行修复",
            "确认修复",
        }
    ),
}

_ERRORS = {
    "commit_capture_draft": "需要用户在独立消息中明确回复「确认存库」后才能入库。",
    "commit_garden_draft": "需要用户明确回复「确认卡片」后才能写入知识卡片库。",
    "apply_librarian_repair": "需要用户明确回复「确认修复」后才能执行知识库修复。",
}

_NEGATIVE_MARKERS = ("不要", "不用", "取消", "先不", "暂不", "别")


def confirmation_error(
    intent: str,
    *,
    confirmation: str = "",
    confirmation_source: str = "agent",
) -> str:
    """Return an error unless a trusted UI action or explicit short phrase confirms the mutation."""
    if (confirmation_source or "").strip().lower() in TRUSTED_CONFIRMATION_SOURCES:
        return ""

    raw = str(confirmation or "").strip()
    if not raw or any(marker in raw for marker in _NEGATIVE_MARKERS):
        return _ERRORS[intent]

    normalized = re.sub(r"[\s，。！？、,.!?；;：:'\"“”‘’（）()]+", "", raw).lower()
    if normalized in _CONFIRMATION_PHRASES[intent]:
        return ""
    return _ERRORS[intent]
