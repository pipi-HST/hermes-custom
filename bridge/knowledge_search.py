"""Lexical knowledge search over captures (contract v1 ``GET /knowledge/search``).

This is deliberately a lexical scan, not a semantic one: it never calls the
Embedder or an LLM, so the endpoint stays cheap and works without API keys.
Hydration goes through :mod:`bridge.capture_view` — importing ``api.routes.*``
here would create a circular import between the API and the bridge layer.
"""
from __future__ import annotations

from typing import Any

from bridge.capture_view import hydrate_capture

DEFAULT_SCAN_LIMIT = 500
MAX_SCAN_LIMIT = 500
_SEARCH_FIELDS = ("title", "summary", "body")


def search_captures(
    vault_path: str,
    repo: Any,
    query: str,
    *,
    limit: int = 10,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
) -> list[dict]:
    """Return hydrated captures matching any whitespace-separated query term.

    Scoring is the number of query terms found (case-insensitive) in
    title/summary/body, ordered by score desc, then ``created_at`` desc, then
    ``id`` desc so equal timestamps still page deterministically.
    """
    terms = [term.lower() for term in str(query or "").split() if term.strip()]
    if not terms:
        return []

    rows = repo.list_all(limit=_clamp_scan_limit(scan_limit))
    matches: list[dict] = []
    for row in rows:
        item = hydrate_capture(vault_path, row)
        haystack = " ".join(str(item.get(field) or "") for field in _SEARCH_FIELDS).lower()
        score = sum(1 for term in terms if term in haystack)
        if score:
            matches.append({**item, "score": score})

    matches.sort(key=_rank_key, reverse=True)
    return matches[: max(0, int(limit))]


def _rank_key(item: dict) -> tuple[int, str, str]:
    return (
        int(item.get("score") or 0),
        _created_at_sort_key(item.get("created_at")),
        str(item.get("id") or ""),
    )


def _created_at_sort_key(value: object) -> str:
    # Rows mix ISO-8601 (Python writes) and ``YYYY-MM-DD HH:MM:SS`` (SQLite
    # defaults); normalise to a comparable prefix, mirroring db.repo's SQL.
    return str(value or "").replace(" ", "T")[:19]


def _clamp_scan_limit(scan_limit: int) -> int:
    try:
        requested = int(scan_limit)
    except (TypeError, ValueError):
        requested = DEFAULT_SCAN_LIMIT
    return max(1, min(requested, MAX_SCAN_LIMIT))
