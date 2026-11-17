"""Detect duplicate Garden knowledge cards before creating new drafts."""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from agents.garden_card_payload import normalize_garden_payload, parse_payload_json


OPEN_DRAFT_STATUSES = ("draft_ready", "revision_requested")


def find_garden_duplicate(conn, candidate: dict[str, Any]) -> dict[str, Any]:
    """Return duplicate status for a proposed Garden card.

    Status values:
    - duplicate: block new draft/card creation
    - possible_duplicate: allow draft creation with warning
    - none: no close match
    """
    candidate_norm = _candidate_view(candidate)
    best = {"status": "none", "score": 0.0, "match": {}, "reasons": []}
    for item in _existing_items(conn):
        score, reasons = _score(candidate_norm, item)
        if score <= best["score"]:
            continue
        status = "none"
        if _is_duplicate(candidate_norm, item, score, reasons):
            status = "duplicate"
        elif _is_possible_duplicate(score, reasons):
            status = "possible_duplicate"
        best = {
            "status": status,
            "score": round(score, 4),
            "match": {
                "id": item["id"],
                "type": item["type"],
                "title": item["title"],
                "one_liner": item["one_liner"],
                "card_kind": item["card_kind"],
            },
            "reasons": reasons,
        }
    if best["status"] == "none":
        return {"status": "none", "score": 0.0, "match": {}, "reasons": []}
    return best


def _existing_items(conn) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in conn.execute(
        """SELECT id, title, card_kind, payload_json, capture_ids
           FROM garden_cards
           WHERE status != 'archived'"""
    ).fetchall():
        items.append(_row_view(dict(row), item_type="garden_card"))

    placeholders = ",".join("?" for _ in OPEN_DRAFT_STATUSES)
    for row in conn.execute(
        f"""SELECT id, title, card_kind, payload_json, capture_ids
            FROM garden_drafts
            WHERE status IN ({placeholders})""",
        OPEN_DRAFT_STATUSES,
    ).fetchall():
        items.append(_row_view(dict(row), item_type="garden_draft"))
    return items


def _candidate_view(candidate: dict[str, Any]) -> dict[str, Any]:
    payload = normalize_garden_payload(candidate.get("payload"), legacy=candidate)
    return {
        "title": payload["title"],
        "one_liner": payload["claim"],
        "card_kind": payload["card_kind"],
        "claims": " ".join(item["statement"] for item in payload["core_judgments"]),
        "capture_ids": set(payload["source_ids"]),
    }


def _row_view(row: dict[str, Any], *, item_type: str) -> dict[str, Any]:
    payload = normalize_garden_payload(parse_payload_json(row.get("payload_json")), legacy=row)
    return {
        "id": str(row.get("id") or ""),
        "type": item_type,
        "title": payload["title"],
        "one_liner": payload["claim"],
        "card_kind": payload["card_kind"],
        "claims": " ".join(item["statement"] for item in payload["core_judgments"]),
        "capture_ids": set(payload["source_ids"]),
    }


def _score(candidate: dict[str, Any], item: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    one_liner_similarity = _text_similarity(
        candidate["one_liner"],
        item["one_liner"],
    )
    title_similarity = _text_similarity(candidate["title"], item["title"])
    claim_similarity = _text_similarity(candidate["claims"], item["claims"])
    source_overlap = _source_overlap(candidate["capture_ids"], item["capture_ids"])
    kind_bonus = 0.08 if candidate["card_kind"] == item["card_kind"] else 0.0

    if one_liner_similarity >= 0.92:
        reasons.append("核心判断几乎相同")
    if title_similarity >= 0.82:
        reasons.append("标题相近")
    if claim_similarity >= 0.78:
        reasons.append("论点相近")
    if source_overlap >= 0.5 and candidate["capture_ids"] and item["capture_ids"]:
        reasons.append("来源材料高度重叠")
    if candidate["card_kind"] == item["card_kind"]:
        reasons.append("卡片类型相同")

    score = max(one_liner_similarity, claim_similarity * 0.9, title_similarity * 0.75)
    score = max(score, source_overlap * 0.85)
    score = min(1.0, score + kind_bonus)
    return score, reasons


def _is_duplicate(
    candidate: dict[str, Any],
    item: dict[str, Any],
    score: float,
    reasons: list[str],
) -> bool:
    if candidate["card_kind"] != item["card_kind"]:
        return False
    if "核心判断几乎相同" in reasons:
        return True
    return score >= 0.9 and "来源材料高度重叠" in reasons


def _is_possible_duplicate(score: float, reasons: list[str]) -> bool:
    if score >= 0.78 and ("论点相近" in reasons or "标题相近" in reasons):
        return True
    return "来源材料高度重叠" in reasons and score >= 0.55


def _text_similarity(left: str, right: str) -> float:
    left_norm = _normalize_text(left)
    right_norm = _normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def _source_overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _normalize_text(text: str) -> str:
    text = re.sub(r"\s+", "", str(text or "").lower())
    return re.sub(r"[，。、“”\"'：:；;,.!?！？（）()\[\]【】\-—_]", "", text)


def _claim_text(arguments: list[Any]) -> str:
    claims: list[str] = []
    for item in arguments:
        if isinstance(item, dict):
            claim = str(item.get("claim") or "").strip()
        else:
            claim = str(item or "").strip()
        if claim:
            claims.append(claim)
    return " ".join(claims)


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
    return value if isinstance(value, list) else []
