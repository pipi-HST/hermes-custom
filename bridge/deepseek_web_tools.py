"""DeepSeek official standalone web Search & Fetch tools.

DeepSeek exposes dedicated tools endpoints that are independent of the chat
completion API and do not require an OpenAI-compatible plugin:

* ``POST https://api.deepseek.com/v1/tools/search``
* ``POST https://api.deepseek.com/v1/tools/fetch``

This module is the single place that knows their payload/response shape so the
research pipeline can drop its DashScope/Qwen search dependency.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
SEARCH_PATH = "/v1/tools/search"
FETCH_PATH = "/v1/tools/fetch"


def _normalize_base_url(base_url: str | None) -> str:
    """Reduce any DeepSeek endpoint URL to the API root.

    Accepts values such as ``https://api.deepseek.com``,
    ``https://api.deepseek.com/v1`` or
    ``https://api.deepseek.com/v1/chat/completions`` and always returns the
    root (``https://api.deepseek.com``) so tool paths can be appended once.
    """
    text = str(base_url or "").strip()
    if not text:
        return DEFAULT_DEEPSEEK_BASE_URL
    text = text.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    text = text.rstrip("/")
    if text.endswith("/v1"):
        text = text[: -len("/v1")]
    text = text.rstrip("/")
    return text or DEFAULT_DEEPSEEK_BASE_URL


def resolve_deepseek_credentials(
    api_key: str | None = None,
    base_url: str | None = None,
) -> tuple[str, str]:
    """Resolve ``(api_key, root_base_url)`` from args, env, then config."""
    key = str(api_key or "").strip()
    if not key:
        key = str(os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    root = str(base_url or "").strip()
    if not root:
        root = str(os.environ.get("DEEPSEEK_BASE_URL") or "").strip()
    if not key or not root:
        try:
            from agents.capture_agent import load_config

            config = load_config()
        except Exception:
            config = None
        if config is not None:
            if not key:
                key = str(getattr(config, "llm_api_key", "") or "").strip()
            if not root:
                root = str(getattr(config, "llm_base_url", "") or "").strip()
    return key, _normalize_base_url(root or DEFAULT_DEEPSEEK_BASE_URL)


def _post_json(
    url: str,
    payload: dict[str, Any],
    api_key: str,
    timeout: float,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        return {}
    # Be tolerant of a ``{"data": {...}}`` envelope.
    if not data.get("results") and isinstance(data.get("data"), dict):
        return data["data"]
    if data.get("error") and not data.get("results"):
        logger.warning("[deepseek_web] %s returned error: %s", url, data.get("error"))
        return {}
    return data


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = -1) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_search_item(item: dict[str, Any]) -> dict[str, Any]:
    snippet = _text(item.get("snippet"))
    summary = _text(item.get("summary")) or snippet
    return {
        "title": _text(item.get("title")),
        "url": _text(item.get("url")),
        "snippet": snippet,
        "summary": summary,
        "description": summary,
        "content": _text(item.get("content")) or summary,
        "site_name": _text(item.get("site_name")),
        "date": _text(item.get("date")),
        "rerank_score": _as_float(item.get("rerank_score"), 0.0),
        "authority": _as_int(
            item.get("authority") if item.get("authority") is not None else -1,
            -1,
        ),
    }


def deepseek_web_search(
    query: str,
    limit: int = 8,
    *,
    ori_question: str | None = None,
    need_content: bool = False,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 15.0,
) -> list[dict[str, Any]]:
    """Search the web through DeepSeek's official ``/v1/tools/search`` API."""
    key, root = resolve_deepseek_credentials(api_key, base_url)
    if not key:
        logger.warning("[deepseek_web] no DeepSeek API key configured; search skipped")
        return []

    payload = {
        "query": (query or "").strip()[:128],
        "count": max(1, min(int(limit or 8), 50)),
        "ori_question": ((ori_question or query or "").strip()[:128]) or None,
        "need_content": bool(need_content),
    }
    data = _post_json(f"{root}{SEARCH_PATH}", payload, key, timeout)
    raw_results = data.get("results") or []
    if not isinstance(raw_results, list):
        return []

    parsed = [
        _normalize_search_item(item)
        for item in raw_results
        if isinstance(item, dict)
    ]
    parsed = [item for item in parsed if item["title"] or item["url"]]
    parsed.sort(
        key=lambda item: (item["rerank_score"], item["authority"]),
        reverse=True,
    )

    strong = [item for item in parsed if item["rerank_score"] >= 1.5]
    if strong:
        weak = [item for item in parsed if item["rerank_score"] < 1.5]
        parsed = strong + weak

    max_results = max(1, int(limit or 8))
    return parsed[:max_results]


def deepseek_web_fetch(
    url: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 20.0,
) -> dict[str, str]:
    """Fetch readable page content through DeepSeek's official ``/v1/tools/fetch``."""
    key, root = resolve_deepseek_credentials(api_key, base_url)
    if not key:
        raise RuntimeError("no DeepSeek API key configured for web fetch")

    data = _post_json(f"{root}{FETCH_PATH}", {"url": url}, key, timeout)
    return {
        "url": url,
        "title": str(data.get("title") or ""),
        "content": str(data.get("content") or ""),
    }
