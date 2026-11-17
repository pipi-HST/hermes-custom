"""Utility functions for research bridge operations."""
from __future__ import annotations

import inspect
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import yaml

from agents.research.types import (
    ResearchEventCallback,
    RESEARCH_ANSWER_TIMEOUT_SECONDS,
    WEB_SEARCH_TIMEOUT_SECONDS,
    WEB_SEARCH_RESERVE_SECONDS,
    WEB_SEARCH_MIN_POST_RESERVE_SECONDS,
    _RESEARCH_DEADLINE,
)


def _coerce_bool(value: Any, *, default: bool) -> bool:
    """Coerce a value to boolean with fallback default."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return default


def _enum_value(value: Any, allowed: set[str], default: str) -> str:
    """Validate and normalize an enum value."""
    if isinstance(value, bool):
        if value and "yes" in allowed:
            return "yes"
        if not value and "no" in allowed:
            return "no"
    text = str(value or "").strip()
    return text if text in allowed else default


def _string_list(value: Any) -> list[str]:
    """Normalize a value to a list of strings."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return _unique_queries([_clip_text(str(item or "").strip(), 80) for item in value if str(item or "").strip()])


def _allowed_usage_list(value: Any) -> list[str]:
    """Filter allowed usage values from input."""
    allowed = {"fact", "background", "interpretation", "quote_context"}
    return [item for item in _string_list(value) if item in allowed][:4]


def _clip_text(text: str, limit: int) -> str:
    """Truncate text to limit with ellipsis preservation."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: int(limit * 0.7)].rstrip() + "\n...\n" + text[-int(limit * 0.25):].lstrip()


def _unique(items: list[str]) -> list[str]:
    """Remove duplicates while preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _unique_queries(items: list[str]) -> list[str]:
    """Remove duplicate queries with normalization."""
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = re.sub(r"\s+", " ", (item or "").strip())
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            out.append(normalized[:220])
    return out


def _domain(url: str) -> str:
    """Extract normalized domain from URL."""
    try:
        host = urlparse(_clean_web_url(url)).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _clean_web_url(value: str) -> str:
    """Extract and clean HTTP URL from text."""
    text = str(value or "").strip()
    match = re.search(r"https?://[^\s，,）)]+", text)
    return match.group(0).rstrip("。；;") if match else text


def _domain_in_list(domain: str, candidates: list[str]) -> bool:
    """Check if domain matches any candidate in list."""
    normalized = domain.lower()
    for candidate in candidates:
        value = candidate.lower().removeprefix("www.")
        if normalized == value or normalized.endswith(f".{value}"):
            return True
    return False


def _domain_matches_required_entity(domain: str, required_terms: list[str]) -> bool:
    """Check if domain contains required entity term."""
    labels = set(re.split(r"[.-]", domain.lower()))
    for term in required_terms:
        normalized = re.sub(r"[^a-z0-9]", "", term.lower())
        if len(normalized) >= 3 and normalized in labels:
            return True
    return False


def _dedupe_web_results(items: list[dict[str, str]]) -> list[dict[str, str]]:
    """Deduplicate web search results by URL or title."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for item in items:
        key = item.get("url") or item.get("title") or item.get("description")
        key = (key or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _json_payload(raw: str | dict[str, Any]) -> dict[str, Any]:
    """Parse JSON payload with YAML fallback."""
    if isinstance(raw, dict):
        return raw
    text = raw or "{}"
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        value = _yaml_payload_or_raise(text, exc)
    return value if isinstance(value, dict) else {"results": value}


def _yaml_payload_or_raise(text: str, json_error: json.JSONDecodeError) -> Any:
    """Parse YAML payload as fallback for JSON failure."""
    try:
        value = yaml.safe_load(text)
    except Exception as exc:
        raise RuntimeError(f"tool returned non-JSON response: {str(json_error)}") from exc
    if isinstance(value, (dict, list)):
        return value
    raise RuntimeError(f"tool returned non-JSON response: {str(json_error)}")


def _parse_web_search(raw: str | dict[str, Any]) -> list[dict[str, str]]:
    """Parse web search API response."""
    payload = _json_payload(raw)
    if payload.get("success") is False:
        raise RuntimeError(str(payload.get("error") or "web_search failed"))
    candidates = payload.get("results")
    if candidates is None:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        candidates = data.get("web") or data.get("results") or []
    if not isinstance(candidates, list):
        return []
    results: list[dict[str, str]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = _clean_web_url(str(item.get("url") or item.get("link") or ""))
        title = str(item.get("title") or item.get("name") or url).strip()
        description = str(
            item.get("description")
            or item.get("snippet")
            or item.get("summary")
            or item.get("content")
            or ""
        ).strip()
        if url or title or description:
            results.append({"url": url, "title": title, "description": description})
    return results


def _parse_web_extract(raw: str | dict[str, Any]) -> dict[str, str]:
    """Parse web extract API response."""
    payload = _json_payload(raw)
    if payload.get("success") is False:
        raise RuntimeError(str(payload.get("error") or "web_extract failed"))
    candidates = payload.get("results")
    if candidates is None:
        data = payload.get("data")
        if isinstance(data, dict):
            candidates = data.get("web") or data.get("results") or data.get("pages")
        elif isinstance(data, list):
            candidates = data
    if not isinstance(candidates, list):
        return {}
    extracted: dict[str, str] = {}
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("source_url") or "").strip()
        content = str(
            item.get("content")
            or item.get("markdown")
            or item.get("text")
            or item.get("summary")
            or item.get("description")
            or ""
        ).strip()
        if url and content:
            extracted[url] = content
    return extracted


def _parse_web_extract_failures(raw: str | dict[str, Any]) -> set[str]:
    """Extract failed URLs from web extract response."""
    payload = _json_payload(raw)
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    if meta.get("allow_summary_fallback") is True:
        return set()
    candidates = payload.get("results")
    if candidates is None:
        data = payload.get("data")
        if isinstance(data, dict):
            candidates = data.get("web") or data.get("results") or data.get("pages")
        elif isinstance(data, list):
            candidates = data
    if not isinstance(candidates, list):
        return set()
    failed: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("source_url") or "").strip()
        if url and item.get("error"):
            failed.add(url)
    return failed


def _first_sentence(text: str) -> str:
    """Extract first sentence from text."""
    match = re.search(r"^(.{20,240}?[。！？.!?])(?:\s|$)", text)
    return match.group(1).strip() if match else text[:240].strip()


def _current_date_for_prompt() -> str:
    """Get current date for prompt injection (supports env override)."""
    return os.environ.get("HERMES_CURRENT_DATE", datetime.now().strftime("%Y-%m-%d"))


def _sanitize_answer_contract(value: dict[str, Any]) -> dict[str, Any]:
    """Sanitize answer contract from planner output."""
    return {
        "primary_answer_first": _coerce_bool(value.get("primary_answer_first"), default=True),
        "explain_selection_relation": _coerce_bool(value.get("explain_selection_relation"), default=False),
        "avoid_unasked_side_topics": _coerce_bool(value.get("avoid_unasked_side_topics"), default=True),
    }


def _sanitize_evidence_contract(value: dict[str, Any]) -> dict[str, Any]:
    """Sanitize evidence contract from planner output."""
    if not isinstance(value, dict):
        value = {}
    minimum = value.get("minimum_independent_sources")
    try:
        minimum_int = int(minimum)
    except (TypeError, ValueError):
        minimum_int = 1
    return {
        "must_cover_subject": _coerce_bool(value.get("must_cover_subject"), default=True),
        "minimum_independent_sources": max(1, min(minimum_int, 5)),
        "prefer_authoritative_sources": _coerce_bool(value.get("prefer_authoritative_sources"), default=True),
        "must_use_primary_source": _coerce_bool(value.get("must_use_primary_source"), default=False),
        "allow_summary_only": _coerce_bool(value.get("allow_summary_only"), default=True),
        "answer_policy": _enum_value(value.get("answer_policy"), {"answer_only_supported"}, "answer_only_supported"),
    }


def _sanitize_source_preferences(value: dict[str, Any]) -> dict[str, Any]:
    """Sanitize source preferences from planner output."""
    return {
        "prefer_primary_sources": _coerce_bool(value.get("prefer_primary_sources"), default=False),
        "preferred_source_kinds": _string_list(value.get("preferred_source_kinds"))[:6],
        "preferred_domains": _string_list(value.get("preferred_domains"))[:8],
        "avoid_topics": _string_list(value.get("avoid_topics"))[:8],
    }


def _sanitize_claim_requirements(value: Any) -> dict[str, dict[str, str]]:
    """Sanitize planner claim requirements into a stable lookup."""
    if isinstance(value, dict):
        value = [
            {"claim_id": key, **(item if isinstance(item, dict) else {})}
            for key, item in value.items()
        ]
    if not isinstance(value, list):
        return {}
    requirements: dict[str, dict[str, str]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        claim_id = _clip_text(str(item.get("claim_id") or item.get("claim") or ""), 120)
        if not claim_id:
            continue
        requirements[claim_id] = {
            "claim_id": claim_id,
            "required_authority": _enum_value(
                item.get("required_authority"),
                {"any_reliable", "primary_or_index", "primary_only"},
                "any_reliable",
            ),
        }
    return requirements


def _search_query_from_private_context(query: str) -> str:
    """Normalize and compactify query for search, removing excess whitespace."""
    normalized = re.sub(r"\s+", " ", query or "").strip()
    if len(normalized) <= 120:
        return normalized.strip(" ?？。！!,，")
    terms = re.findall(r"[A-Za-z0-9\u0370-\u03ff][A-Za-z0-9._\-\u0370-\u03ff]{1,}[\u4e00-\u9fff]?|[\u4e00-\u9fff]{2,}", normalized)
    if not terms:
        return normalized[:200]
    compact = " ".join(terms[:18])
    return compact


def _required_query_terms(query: str) -> list[str]:
    """Extract required query terms from patterns like 'what is X' or 'who is X'."""
    text = re.sub(r"\s+", " ", query or "").strip(" ?？。！!,，")
    if not text:
        return []
    candidates: list[str] = []
    patterns = [
        r"^什么是\s+(.+)$",
        r"^(.+?)\s*(?:是谁|是什么|是什么意思|什么意思|简介|介绍|个人资料)$",
        r"^为什么说\s*(.+?)\s*(?:是|会|成了|成为|属于|叫做).+$",
        r"^为什么\s*(.+?)\s*(?:是|会|成了|成为|属于).+$",
    ]
    for pattern in patterns:
        match = re.match(pattern, text, flags=re.I)
        if match:
            candidates.append(match.group(1).strip(" ?？。！!,，"))
    terms: list[str] = []
    context_only_terms = {"这", "这个", "该", "此", "它", "其", "this", "it"}
    for candidate in candidates:
        if not candidate:
            continue
        if candidate.lower() in context_only_terms:
            continue
        if re.search(r"[\u4e00-\u9fff]", candidate) and len(candidate) <= 12:
            terms.append(candidate)
            continue
        terms.extend(re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]{1,}", candidate))
    return _unique_queries(terms)[:4]


def _extract_core_terms(query: str) -> list[str]:
    """Extract core terms from a query string."""
    text = query or ""
    subject_terms = _required_query_terms(text)
    chunks = re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]{1,}[\u4e00-\u9fff]?|[\u4e00-\u9fff]{2,}", text)
    terms = list(subject_terms)
    for term in chunks:
        value = term.strip()
        if not value:
            continue
        terms.append(value)
    return _unique_queries(terms)[:12]


def _contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value or ""))


def _claim_requires_observed_metric_value(claim_id: str) -> bool:
    normalized = str(claim_id or "").lower()
    return bool(
        re.search(
            r"(impact|factor|metric|value|market_cap|marketcap|price|rank|ranking|score|ratio|rate|amount|number|市值|影响因子|排名|数值|价格|金额|比率)",
            normalized,
        )
    )


def _source_has_observed_metric_value(source: dict[str, Any]) -> bool:
    text = " ".join(
        str(source.get(key) or "")
        for key in ("title", "description", "excerpt")
    )
    if re.search(r"[$¥￥]\s*\d", text):
        return True
    if re.search(r"\bq[1-4]\b", text, flags=re.I):
        return True
    for match in re.finditer(r"(?<![\w.-])\d+(?:\.\d+)?\s*(?:%|倍|分|万|亿|万亿|million|billion|trillion|bn|m)?", text, flags=re.I):
        token = match.group(0).strip()
        number = re.search(r"\d+(?:\.\d+)?", token)
        if not number:
            continue
        value = number.group(0)
        if re.fullmatch(r"(?:19|20)\d{2}", value):
            continue
        around = text[max(0, match.start() - 8) : match.end() + 8]
        if re.search(r"\b(?:issn|eissn)\b", around, flags=re.I):
            continue
        if "." in value or re.search(r"%|倍|分|万|亿|million|billion|trillion|bn|[$¥￥]", token, flags=re.I):
            return True
    return False


# ---------------------------------------------------------------------------
# Research budget / timeout utilities
# ---------------------------------------------------------------------------


def _start_research_budget() -> None:
    _RESEARCH_DEADLINE.set(time.monotonic() + max(5.0, RESEARCH_ANSWER_TIMEOUT_SECONDS))


def _remaining_research_budget() -> float | None:
    deadline = _RESEARCH_DEADLINE.get()
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _stage_timeout(default_seconds: float, *, reserve_seconds: float = 0.0) -> float:
    remaining = _remaining_research_budget()
    if remaining is None:
        return max(1.0, float(default_seconds or 1.0))
    usable = remaining - max(0.0, float(reserve_seconds or 0.0))
    return max(0.0, min(float(default_seconds or 1.0), usable))


def _research_budget_available(min_seconds: float = 1.0) -> bool:
    remaining = _remaining_research_budget()
    return remaining is None or remaining >= min_seconds


def _web_search_stage_timeout() -> float:
    timeout = _stage_timeout(WEB_SEARCH_TIMEOUT_SECONDS, reserve_seconds=WEB_SEARCH_RESERVE_SECONDS)
    if timeout >= 1.0:
        return timeout
    remaining = _remaining_research_budget()
    if remaining is None:
        return max(1.0, WEB_SEARCH_TIMEOUT_SECONDS)
    usable = remaining - max(0.0, WEB_SEARCH_MIN_POST_RESERVE_SECONDS)
    return max(0.0, min(WEB_SEARCH_TIMEOUT_SECONDS, usable))


async def _emit_research_event_async(
    event_callback: ResearchEventCallback | None,
    event: str,
    **kwargs: Any,
) -> None:
    """Emit a research event via the callback if provided."""
    if event_callback is None:
        return
    payload = {"type": event, **kwargs}
    result = event_callback(payload)
    if inspect.isawaitable(result):
        await result


def _ensure_main_repo_on_path() -> None:
    configured = os.environ.get("HERMES_AGENT_MAIN_ROOT", "").strip()
    if configured:
        main_root = Path(configured).expanduser()
    else:
        # Sibling checkout by default; no per-machine absolute path.
        main_root = Path(__file__).resolve().parents[3] / "hermes-agent-main"
    if main_root.is_dir() and str(main_root) not in sys.path:
        sys.path.insert(0, str(main_root))
