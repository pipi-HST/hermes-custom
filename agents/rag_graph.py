"""Deterministic one-hop graph expansion for RAG retrieval."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from agents.embedder import cosine_similarity
from agents.garden_card_payload import payload_from_row
from agents.managed_sections import is_managed_section_title
from agents.markdown_writer import read_note
from db.embedding_repo import CaptureEmbeddingRepo

_THEME_LINK_RE = re.compile(r"\[\[themes/([^|\]]+)(?:\|([^\]]+))?\]\]")

logger = logging.getLogger(__name__)


def _table_columns(conn, table: str) -> set[str]:
    try:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    except Exception:
        # A locked/corrupt database must surface; only a missing table degrades.
        raise
    return {str(row["name"]) for row in rows}


def build_graph_retrieval(
    conn,
    *,
    query_embedding: bytes,
    seed_hits: list[dict[str, Any]],
    vault_path: str,
    final_limit: int = 6,
    max_garden: int = 3,
    max_theme: int = 3,
    max_expanded_captures: int = 4,
) -> dict[str, Any]:
    seed_capture_ids = [
        str(hit.get("id") or hit.get("capture_id") or "")
        for hit in seed_hits
        if hit.get("id") or hit.get("capture_id")
    ]
    garden_cards = _garden_cards_for_captures(conn, seed_capture_ids, limit=max_garden)
    linked_card_ids = _linked_card_ids(garden_cards)
    if linked_card_ids:
        garden_cards.extend(_garden_cards_by_ids(conn, linked_card_ids, limit=max_garden))
        garden_cards = _dedupe_by_id(garden_cards)[:max_garden]

    theme_groups = _theme_groups_for_captures(conn, seed_capture_ids, limit=max_theme)
    theme_pages = _theme_pages_for_captures(seed_hits, vault_path, limit=max_theme)
    # Filter archived candidates before the expansion budget is spent, so an
    # archived sibling cannot consume a slot and starve an active one.
    candidate_capture_ids = [
        *seed_capture_ids,
        *[
            str(capture_id)
            for card in garden_cards
            for capture_id in card.get("capture_ids", [])
        ],
    ]
    active_capture_ids = set(
        _active_capture_ids(conn, list(dict.fromkeys(candidate_capture_ids)))
    )
    expanded_capture_ids = _expanded_capture_ids(
        seed_capture_ids,
        garden_cards,
        theme_groups,
        max_expanded_captures,
        allowed_capture_ids=active_capture_ids,
    )
    final_hits = _select_final_hits(
        conn,
        query_embedding=query_embedding,
        seed_hits=seed_hits,
        expanded_capture_ids=expanded_capture_ids,
        final_limit=final_limit,
    )
    stopped_reason = "no_expansion"
    if expanded_capture_ids:
        stopped_reason = "enough_evidence"
    if len(expanded_capture_ids) >= max_expanded_captures:
        stopped_reason = "budget"
    return {
        "seed_hits": seed_hits,
        "expanded_garden_cards": garden_cards,
        "expanded_theme_groups": theme_groups,
        "expanded_theme_pages": theme_pages,
        "expanded_capture_ids": expanded_capture_ids,
        "final_hits": final_hits,
        "trace": {
            "seed_count": len(seed_hits),
            "garden_count": len(garden_cards),
            "theme_count": len(theme_groups) + len(theme_pages),
            "expanded_capture_count": len(expanded_capture_ids),
            "final_count": len(final_hits),
            "stopped_reason": stopped_reason,
        },
    }


def _garden_cards_for_captures(
    conn,
    capture_ids: list[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    wanted = set(capture_ids)
    if not wanted or not _table_columns(conn, "garden_cards"):
        return []
    rows = conn.execute(
        """SELECT id, title, payload_json, capture_ids
           FROM garden_cards
           WHERE status != 'archived'
           ORDER BY updated_at DESC"""
    ).fetchall()
    cards = []
    for row in rows:
        card_capture_ids = _json_list(row["capture_ids"])
        if wanted & set(card_capture_ids):
            cards.append(_garden_card_dict(row, card_capture_ids))
        if len(cards) >= limit:
            break
    return cards


def _garden_cards_by_ids(conn, card_ids: list[str], *, limit: int) -> list[dict[str, Any]]:
    if not card_ids or not _table_columns(conn, "garden_cards"):
        return []
    placeholders = ",".join("?" for _ in card_ids)
    rows = conn.execute(
        f"""SELECT id, title, payload_json, capture_ids
            FROM garden_cards
            WHERE id IN ({placeholders}) AND status != 'archived'
            ORDER BY updated_at DESC""",
        tuple(card_ids),
    ).fetchall()
    return [_garden_card_dict(row, _json_list(row["capture_ids"])) for row in rows[:limit]]


def _theme_groups_for_captures(
    conn,
    capture_ids: list[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    wanted = set(capture_ids)
    if not wanted:
        return []
    rows = _pending_question_rows(conn)
    if rows is None:
        return []
    groups = []
    for row in rows:
        related = _json_list(row["related_capture_ids"])
        if wanted & set(related):
            groups.append(
                {
                    "id": row["id"],
                    "title": row["question_text"],
                    "capture_ids": related,
                    "status": row["status"],
                }
            )
        if len(groups) >= limit:
            break
    return groups


def _pending_question_rows(conn):
    """Return pending-question rows in the ``theme_questions`` row shape.

    The real knowledge base stores theme candidates in ``pending_questions``.
    Older builds (and some test fixtures) used a ``theme_questions`` table with
    ``question_text``/``related_capture_ids``, while an earlier iteration of
    this module addressed ``pending_questions`` with ``theme_name``/``question``
    /``capture_ids`` columns.  Try each shape and return ``None`` when none of
    them exist so callers can degrade gracefully.
    """
    pending_columns = _table_columns(conn, "pending_questions")
    if pending_columns:
        if {"theme_name", "question", "capture_ids"} <= pending_columns:
            return conn.execute(
                """SELECT id, COALESCE(NULLIF(theme_name, ''), question) AS question_text,
                          capture_ids AS related_capture_ids, status
                   FROM pending_questions
                   WHERE COALESCE(status, '') != 'dismissed'
                   ORDER BY created_at DESC"""
            ).fetchall()
        if {"question_text", "related_capture_ids"} <= pending_columns:
            return conn.execute(
                """SELECT id, question_text, related_capture_ids, status
                   FROM pending_questions
                   WHERE COALESCE(status, '') != 'dismissed'
                   ORDER BY created_at DESC"""
            ).fetchall()
        logger.warning("[RAG graph] pending_questions has unexpected columns: %s", pending_columns)
        return None

    legacy_columns = _table_columns(conn, "theme_questions")
    if {"question_text", "related_capture_ids"} <= legacy_columns:
        return conn.execute(
            """SELECT id, question_text, related_capture_ids, status
               FROM theme_questions
               ORDER BY created_at DESC"""
        ).fetchall()
    return None


def _theme_pages_for_captures(
    seed_hits: list[dict[str, Any]],
    vault_path: str,
    *,
    limit: int,
) -> list[dict[str, str]]:
    vault = Path(vault_path).expanduser()
    pages: list[dict[str, str]] = []
    seen: set[str] = set()
    for hit in seed_hits:
        file_path = str(hit.get("file_path") or "").strip()
        if not file_path:
            continue
        note = read_note(vault, file_path)
        if not note:
            continue
        frontmatter, body = note
        for name in _frontmatter_theme_names(frontmatter):
            _append_theme_page(pages, seen, name, f"themes/{name}.md")
        for match in _THEME_LINK_RE.finditer(body):
            target = match.group(1).strip()
            title = (match.group(2) or target).strip()
            _append_theme_page(pages, seen, title, f"themes/{target}.md")
        if len(pages) >= limit:
            break
    return pages[:limit]


def _select_final_hits(
    conn,
    *,
    query_embedding: bytes,
    seed_hits: list[dict[str, Any]],
    expanded_capture_ids: list[str],
    final_limit: int,
) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for hit in seed_hits:
        capture_id = str(hit.get("id") or hit.get("capture_id") or "")
        matched_kind = hit.get("matched_kind") or hit.get("kind") or ""
        if not capture_id or not matched_kind:
            continue
        item = dict(hit)
        item["id"] = capture_id
        item["matched_kind"] = matched_kind
        key = (capture_id, str(item.get("section_id") or ""))
        by_key[key] = item

    repo = CaptureEmbeddingRepo(conn)
    for capture_id in expanded_capture_ids:
        for row in repo.list_by_capture(capture_id):
            if row.get("kind") == "section" and is_managed_section_title(
                row.get("section_title", "")
            ):
                continue
            if row.get("kind") not in ("identity", "section"):
                continue
            item = dict(row)
            item["id"] = item["capture_id"]
            item["matched_kind"] = item["kind"]
            item["score"] = cosine_similarity(query_embedding, item["embedding"])
            key = (item["capture_id"], item.get("section_id") or "")
            existing = by_key.get(key)
            if existing is None or item["score"] > existing.get("score", 0):
                by_key[key] = item

    ranked = sorted(by_key.values(), key=lambda item: item.get("score", 0), reverse=True)
    ranked = _one_hit_per_capture(ranked)
    return ranked[:final_limit]


def _one_hit_per_capture(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep at most one hit per capture: the best section, else the identity.

    Retrieval used to return *every* section of an expanded capture, so the
    prompt and ``source_map`` numbered the same document several times and one
    note could crowd out cross-note diversity.  ``hits`` is already ranked by
    score, so the first row seen for a capture wins within its kind.
    """
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for hit in hits:
        capture_id = str(hit.get("id") or hit.get("capture_id") or "")
        if not capture_id:
            continue
        is_section = _is_concrete_section(hit)
        existing = best.get(capture_id)
        if existing is None:
            best[capture_id] = hit
            order.append(capture_id)
            continue
        # A concrete section always beats the whole-note hit; between two
        # sections the earlier (higher scoring) one already won.
        if is_section and not _is_concrete_section(existing):
            best[capture_id] = hit
    return [best[capture_id] for capture_id in order]


def _is_concrete_section(hit: dict[str, Any]) -> bool:
    return bool(
        hit.get("matched_kind") == "section"
        and (hit.get("section_id") or hit.get("section_title"))
    )


# Backwards-compatible alias: scripts and older imports still use the old name.
_prefer_sections_over_whole_note = _one_hit_per_capture


def _expanded_capture_ids(
    seed_capture_ids: list[str],
    garden_cards: list[dict[str, Any]],
    theme_groups: list[dict[str, Any]],
    limit: int,
    *,
    allowed_capture_ids: set[str] | None = None,
) -> list[str]:
    """Expand through cards only.

    ``theme_groups`` used to dump every sibling article of the same topic into
    the candidate pool; a broad topic like "Agent 与大模型" would drag in a
    dozen loosely related long reads.  Theme groups still feed the orientation
    line in the prompt, but they no longer widen retrieval.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for capture_id in seed_capture_ids:
        if allowed_capture_ids is None or capture_id in allowed_capture_ids:
            _append_capture_id(ordered, seen, capture_id, limit)
    for card in garden_cards:
        for capture_id in card.get("capture_ids", []):
            capture_id = str(capture_id)
            if allowed_capture_ids is not None and capture_id not in allowed_capture_ids:
                continue
            _append_capture_id(ordered, seen, capture_id, limit)
    return ordered


def _active_capture_ids(conn, capture_ids: list[str]) -> list[str]:
    """Drop archived captures from graph expansion while keeping score order."""
    if not capture_ids:
        return []
    if "archived_at" not in _table_columns(conn, "captures"):
        return list(capture_ids)
    placeholders = ",".join("?" for _ in capture_ids)
    rows = conn.execute(
        f"""SELECT id FROM captures
            WHERE id IN ({placeholders}) AND archived_at IS NULL""",
        tuple(capture_ids),
    ).fetchall()
    active = {str(row["id"]) for row in rows}
    return [capture_id for capture_id in capture_ids if capture_id in active]


def _garden_card_dict(row, capture_ids: list[str]) -> dict[str, Any]:
    payload = payload_from_row(row)
    return {
        "id": row["id"],
        "title": row["title"],
        "one_liner": payload["claim"],
        "core_arguments": [
            {"claim": item["statement"]} for item in payload["core_judgments"]
        ],
        "knowledge_links": payload["related_card_ids"],
        "capture_ids": capture_ids,
    }


def _linked_card_ids(cards: list[dict[str, Any]]) -> list[str]:
    linked = []
    for card in cards:
        for card_id in card.get("knowledge_links", []):
            if isinstance(card_id, str) and card_id:
                linked.append(card_id)
    return linked


def _dedupe_by_id(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for item in items:
        item_id = item.get("id")
        if item_id in seen:
            continue
        seen.add(item_id)
        result.append(item)
    return result


def _frontmatter_theme_names(frontmatter: dict[str, Any]) -> list[str]:
    linked = frontmatter.get("linked_themes") or []
    if isinstance(linked, str):
        linked = [linked]
    return [str(item).strip() for item in linked if str(item).strip()]


def _append_theme_page(
    pages: list[dict[str, str]],
    seen: set[str],
    title: str,
    path: str,
) -> None:
    if path in seen:
        return
    seen.add(path)
    pages.append({"title": title, "path": path})


def _append_capture_id(
    ordered: list[str],
    seen: set[str],
    capture_id: str,
    limit: int,
) -> None:
    if not capture_id or capture_id in seen or len(ordered) >= limit:
        return
    seen.add(capture_id)
    ordered.append(capture_id)


def _json_list(text: Any) -> list[Any]:
    try:
        value = json.loads(text or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []
