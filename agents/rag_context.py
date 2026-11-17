"""Build enriched context for RAG without changing the public tool surface."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agents.garden_card_payload import payload_from_row
from agents.markdown_writer import read_note

_THEME_LINK_RE = re.compile(r"\[\[themes/([^|\]]+)(?:\|([^\]]+))?\]\]")


def build_rag_context_pack(
    conn,
    *,
    capture_hits: list[dict[str, Any]],
    vault_path: str,
    graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    graph = graph or {}
    theme_pages = graph.get("expanded_theme_pages") or _theme_pages_for_captures(
        capture_hits,
        vault_path,
    )
    garden_cards = graph.get("expanded_garden_cards") or _garden_cards_for_captures(
        conn, [str(hit.get("id", "")) for hit in capture_hits]
    )
    theme_groups = graph.get("expanded_theme_groups") or []
    return {
        "captures": capture_hits,
        "capture_count": len(capture_hits),
        "garden_cards": garden_cards,
        "garden_count": len(garden_cards),
        "theme_groups": theme_groups,
        "theme_group_count": len(theme_groups),
        "theme_pages": theme_pages,
        "theme_count": len(theme_pages),
        "graph_trace": graph.get("trace") or {},
    }


def _garden_cards_for_captures(conn, capture_ids: list[str]) -> list[dict[str, Any]]:
    wanted = {cid for cid in capture_ids if cid}
    if not wanted:
        return []
    try:
        rows = conn.execute(
            """SELECT id, title, payload_json, capture_ids
               FROM garden_cards
               ORDER BY updated_at DESC"""
        ).fetchall()
    except Exception:
        return []

    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        card_capture_ids = set(_json_list(row["capture_ids"]))
        if not (wanted & card_capture_ids):
            continue
        card_id = str(row["id"])
        if card_id in seen:
            continue
        seen.add(card_id)
        payload = payload_from_row(row)
        cards.append(
            {
                "id": card_id,
                "title": row["title"],
                "one_liner": payload["claim"],
                "core_arguments": [
                    {"claim": item["statement"]}
                    for item in payload["core_judgments"]
                ],
                "capture_ids": list(card_capture_ids),
            }
        )
        if len(cards) >= 3:
            break
    return cards


def _theme_pages_for_captures(
    capture_hits: list[dict[str, Any]],
    vault_path: str,
) -> list[dict[str, str]]:
    themes: list[dict[str, str]] = []
    seen: set[str] = set()
    vault = Path(vault_path).expanduser()
    for hit in capture_hits:
        file_path = str(hit.get("file_path", ""))
        if not file_path:
            continue
        note = read_note(vault, file_path)
        if not note:
            continue
        frontmatter, body = note
        for name in _frontmatter_theme_names(frontmatter):
            _append_theme(themes, seen, name, f"themes/{name}.md")
        for match in _THEME_LINK_RE.finditer(body):
            target = match.group(1).strip()
            title = (match.group(2) or target).strip()
            _append_theme(themes, seen, title, f"themes/{target}.md")
        if len(themes) >= 3:
            break
    return themes


def _frontmatter_theme_names(frontmatter: dict[str, Any]) -> list[str]:
    linked = frontmatter.get("linked_themes") or []
    if isinstance(linked, str):
        linked = [linked]
    return [str(item).strip() for item in linked if str(item).strip()]


def _append_theme(
    themes: list[dict[str, str]],
    seen: set[str],
    title: str,
    path: str,
) -> None:
    key = path or title
    if key in seen:
        return
    seen.add(key)
    themes.append({"title": title, "path": path})


def _json_list(text: Any) -> list[Any]:
    try:
        value = json.loads(text or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []
