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


def _query_terms(query: str) -> list[str]:
    import re

    return [
        term.lower()
        for term in re.findall(r"[0-9A-Za-z_]{2,}|[\u4e00-\u9fff]{2,}", str(query or ""))
    ]


def _fts_candidates(repo: Any, query: str, *, limit: int) -> list[Any]:
    """Return capture rows via the CJK-tokenised FTS index, or [] on fallback."""
    try:
        from db.fts_tokenizer import build_fts_match_query, build_fts_or_query

        terms = _query_terms(query)
        match = build_fts_match_query(terms) or build_fts_or_query(terms)
        if not match:
            return []
        rows = repo.conn.execute(
            """SELECT capture_id
               FROM capture_search_fts
               WHERE capture_search_fts MATCH ? AND kind != 'card'
               LIMIT ?""",
            (match, max(1, int(limit) * 3)),
        ).fetchall()
        capture_ids: list[str] = []
        for row in rows:
            capture_id = str(row["capture_id"] or "")
            if capture_id and capture_id not in capture_ids:
                capture_ids.append(capture_id)
        result = []
        for capture_id in capture_ids:
            capture = repo.get_by_id(capture_id)
            if capture:
                result.append(capture)
        return result
    except Exception:
        return []


def search_captures(
    vault_path: str,
    repo: Any,
    query: str,
    *,
    limit: int = 10,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
) -> list[dict]:
    """Return hydrated captures matching any whitespace-separated query term.

    The FTS path indexes CJK bigrams, so Chinese queries work without spaces.
    Only matching captures are hydrated from disk; the old 500-file scan remains
    as a compatibility fallback for databases without FTS5.
    """
    terms = [term.lower() for term in str(query or "").split() if term.strip()]
    if not terms:
        return []

    rows = _fts_candidates(repo, query, limit=limit)
    used_fts = bool(rows)
    if not rows:
        rows = repo.list_all(limit=_clamp_scan_limit(scan_limit))
    matches: list[dict] = []
    for row in rows:
        item = hydrate_capture(vault_path, row)
        haystack = " ".join(str(item.get(field) or "") for field in _SEARCH_FIELDS).lower()
        score = sum(1 for term in terms if term in haystack)
        if score:
            matches.append({**item, "score": score})

    # FTS may find a bigram candidate that the whitespace-term scorer cannot
    # confirm (punctuation or a one-character term).  Fall back rather than
    # returning an empty list for a real index hit.
    if used_fts and not matches:
        for row in repo.list_all(limit=_clamp_scan_limit(scan_limit)):
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
