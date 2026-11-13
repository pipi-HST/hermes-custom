"""Deterministic confirmation gates for durable Knowledge mutations.

Two complementary paths exist:

* Agent/chat path — the user types a short confirmation phrase; the phrase is
  matched here (``confirmation_error``).
* HTTP path — the dashboard first obtains a single-use token
  (:class:`db.confirmation_repo.ConfirmationRepo`) and the route burns it.  The
  route then calls the bridge with ``token_verified=True``.

There is deliberately **no** "trusted source" string that skips the gate: the
previous ``confirmation_source in {"api","dashboard","manual"}`` shortcut let
any HTTP client write durable knowledge without user interaction.
"""

from __future__ import annotations

import re


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
    "commit_reading_artifact": frozenset(
        {
            "可以保存",
            "可以整理",
            "保存吧",
            "确认整理",
            "确认保存",
            "整理入库",
            "入库",
            "存库",
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
    "commit_reading_artifact": "需要用户明确回复「确认整理」后才能保存阅读整理结果。",
    "commit_garden_draft": "需要用户明确回复「确认卡片」后才能写入知识卡片库。",
    "apply_librarian_repair": "需要用户明确回复「确认修复」后才能执行知识库修复。",
}

_DEFAULT_ERROR = "该操作需要用户明确确认后才能执行。"

_NEGATIVE_MARKERS = ("不要", "不用", "取消", "先不", "暂不", "别")


def confirmation_error(
    intent: str,
    *,
    confirmation: str = "",
    confirmation_source: str = "agent",
    token_verified: bool = False,
) -> str:
    """Return an error unless the mutation is confirmed.

    ``token_verified`` must only be set by a server-side HTTP route that has
    already consumed a single-use confirmation token for this exact object.
    """
    if token_verified:
        return ""

    source = (confirmation_source or "").strip().lower()
    if source and source != "agent":
        # Non-agent callers have no phrase to show; they must present a token.
        return _ERRORS.get(intent, _DEFAULT_ERROR)

    raw = str(confirmation or "").strip()
    if not raw or any(marker in raw for marker in _NEGATIVE_MARKERS):
        return _ERRORS.get(intent, _DEFAULT_ERROR)

    normalized = re.sub(r"[\s，。！？、,.!?；;：:'\"“”‘’（）()]+", "", raw).lower()
    phrases = _CONFIRMATION_PHRASES.get(intent)
    if phrases is None:
        return _ERRORS.get(intent, _DEFAULT_ERROR)
    if normalized in phrases:
        return ""
    return _ERRORS.get(intent, _DEFAULT_ERROR)


_ALL_CONFIRMATION_PHRASES = frozenset().union(*_CONFIRMATION_PHRASES.values())


def redact_confirmation_phrases(text: str) -> str:
    """Remove confirmation phrases from untrusted content before prompting.

    Retrieved web pages / imported documents must never be able to talk the
    model into "confirming" a durable write.  Redacting the phrases is
    defence-in-depth on top of the rule that only the raw user turn may confirm.
    """
    result = str(text or "")
    for phrase in sorted(_ALL_CONFIRMATION_PHRASES, key=len, reverse=True):
        if phrase in result:
            result = result.replace(phrase, "[确认短语已移除]")
    return result


def contains_confirmation_phrase(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？、,.!?；;：:'\"“”‘’（）()]+", "", str(text or "")).lower()
    return any(phrase in normalized for phrase in _ALL_CONFIRMATION_PHRASES)
