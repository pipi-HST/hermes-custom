"""Deterministic source-slot ranking for Hermes knowledge retrieval.

The original retriever concatenated ``cards + evidence[:n] + keywords``.  A
card therefore always occupied a front seat, even when it was an isolated note
with no underlying source.  This module keeps the product requirement that
first-hand evidence is guaranteed a seat, while still allowing a well-supported
card to lead.

The weights are intentionally data-classed so they can be tuned/calibrated
without editing the retrieval loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class RankingWeights:
    dual_hit_bonus: float = 0.08
    source_weight: float = 0.04
    citation_weight: float = 0.02
    authority_cap: float = 0.12
    isolated_card_penalty: float = 0.06


DEFAULT_MAX_SOURCES = 7
DEFAULT_EVIDENCE_RESERVED = 2
DEFAULT_KEYWORD_RESERVED = 2
DEFAULT_MAX_CARDS = 2


def card_score(card: dict[str, Any], weights: RankingWeights = RankingWeights()) -> float:
    """Return the product score for one knowledge card.

    ``source_count`` is the number of captures the card cites.  ``citation_count``
    is the historical reuse counter maintained by ``citation_events``.
    """
    vector = float(card.get("vec_score") or 0.0)
    literal = float(card.get("literal_score") or 0.0)
    source_count = int(card.get("source_count") or 0)
    citation_count = int(card.get("citation_count") or 0)

    score = max(vector, literal)
    if vector > 0 and literal > 0:
        score += weights.dual_hit_bonus
    score += min(
        weights.source_weight * min(source_count, 2)
        + weights.citation_weight * min(citation_count, 2),
        weights.authority_cap,
    )
    if source_count == 0:
        score -= weights.isolated_card_penalty
    return round(score, 6)


def _identity(item: dict[str, Any]) -> tuple[str, str]:
    kind = str(item.get("source_kind") or item.get("matched_kind") or "")
    item_id = str(item.get("id") or item.get("capture_id") or "")
    return kind, item_id


def _dedupe(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _identity(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _score(item: dict[str, Any]) -> float:
    try:
        return float(item.get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_card(item: dict[str, Any]) -> bool:
    return str(item.get("matched_kind") or item.get("source_kind") or "") == "garden_card"


def _source_count(item: dict[str, Any]) -> int:
    try:
        return int(item.get("source_count") or 0)
    except (TypeError, ValueError):
        return 0


def rank_sources(
    *,
    evidence: list[dict[str, Any]],
    keywords: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    max_sources: int = DEFAULT_MAX_SOURCES,
    evidence_reserved: int = DEFAULT_EVIDENCE_RESERVED,
    keyword_reserved: int = DEFAULT_KEYWORD_RESERVED,
    max_cards: int = DEFAULT_MAX_CARDS,
    weights: RankingWeights = RankingWeights(),
) -> list[dict[str, Any]]:
    """Select and order the final prompt sources.

    Invariants:
    * at most ``max_sources`` items;
    * when available, at least ``evidence_reserved`` first-hand evidence items
      are present;
    * at most ``max_cards`` cards are present;
    * an isolated card (``source_count == 0``) never ranks above any evidence;
    * at most one card may precede the first evidence item.
    """
    evidence = _dedupe(evidence)
    keywords = _dedupe(keywords)
    cards = _dedupe(cards)

    # Cards are scored once; the retrieval caller already attached component
    # scores and source/citation counts.
    for card in cards:
        card["score"] = card_score(card, weights)
    cards.sort(key=_score, reverse=True)

    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str]] = set()

    def add(item: dict[str, Any]) -> bool:
        if len(selected) >= max_sources:
            return False
        key = _identity(item)
        if key in selected_keys:
            return False
        selected.append(item)
        selected_keys.add(key)
        return True

    for item in evidence[: max(0, evidence_reserved)]:
        add(item)

    keywords_added = 0
    for item in keywords:
        if keywords_added >= keyword_reserved:
            break
        if add(item):
            keywords_added += 1

    cards_added = 0
    for card in cards:
        if cards_added >= max_cards:
            break
        if add(card):
            cards_added += 1

    # Fill remaining seats from evidence/keywords/cards by score, preserving
    # the guarantee that an isolated card cannot outrank evidence.
    rest = _dedupe([*evidence, *keywords, *cards])
    rest.sort(key=_score, reverse=True)
    for item in rest:
        if len(selected) >= max_sources:
            break
        if _is_card(item) and cards_added >= max_cards:
            continue
        if add(item) and _is_card(item):
            cards_added += 1

    # Order: evidence first, then non-isolated cards, then isolated cards.
    # A non-isolated card may lead only if it is the single card ahead of the
    # first evidence item; everything else is score-ordered within its bucket.
    evidence_items = [item for item in selected if not _is_card(item)]
    card_items = [item for item in selected if _is_card(item)]
    supported_cards = [item for item in card_items if _source_count(item) > 0]
    isolated_cards = [item for item in card_items if _source_count(item) == 0]

    evidence_items.sort(key=_score, reverse=True)
    supported_cards.sort(key=_score, reverse=True)
    isolated_cards.sort(key=_score, reverse=True)

    ordered: list[dict[str, Any]] = []
    if supported_cards and evidence_items and _score(supported_cards[0]) >= _score(evidence_items[0]):
        ordered.append(supported_cards.pop(0))
    ordered.extend(evidence_items)
    ordered.extend(supported_cards)
    ordered.extend(isolated_cards)

    # Keep the total bounded even if a caller passes a tiny max_sources.
    return ordered[:max_sources]
