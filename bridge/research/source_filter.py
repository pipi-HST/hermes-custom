"""Filters for combining local knowledge with an explicit web-search turn."""
from __future__ import annotations

from typing import Any, Iterable

DEFAULT_LOCAL_WEB_MIN_SCORE = 0.42


def filter_local_sources_for_web(
    sources: Iterable[dict[str, Any]],
    *,
    min_score: float = DEFAULT_LOCAL_WEB_MIN_SCORE,
    keep_ids: Iterable[str] = (),
    keep_paths: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Drop only weak, pure-vector local notes when the user forced web search.

    Cards, sources without a score, and the currently-open article are always
    retained.  A source without a score is kept because dropping it would hide
    a literal/FTS hit whose score is not expressed on the same cosine scale.
    """
    kept_ids = {str(value).strip() for value in keep_ids if str(value).strip()}
    kept_paths = {str(value).strip() for value in keep_paths if str(value).strip()}
    kept: list[dict[str, Any]] = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        source_kind = str(source.get("source_kind") or source.get("type") or "").lower()
        if source_kind in {"garden_card", "knowledge_card"}:
            kept.append(source)
            continue
        source_id = str(source.get("id") or "").strip()
        source_path = str(source.get("file_path") or source.get("path") or "").strip()
        if source_id in kept_ids or (source_path and source_path in kept_paths):
            kept.append(source)
            continue
        raw_score = source.get("score")
        if raw_score is None or raw_score == "":
            kept.append(source)
            continue
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            kept.append(source)
            continue
        if score >= float(min_score):
            kept.append(source)
    return kept
