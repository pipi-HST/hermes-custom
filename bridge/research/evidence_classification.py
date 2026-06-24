"""Evidence source classification and subject-title helpers."""
from __future__ import annotations

import re
from typing import Any

from bridge.research.planner import (
    _source_contains_term,
    _subject_terms,
)
from bridge.research.search import (
    _is_generic_homepage_url,
    _web_sources_from_ranked,
)
from bridge.research.utils import (
    _contains_cjk,
    _string_list,
    _unique,
)


def _source_title_conflicts_with_subject(source: dict[str, Any], research_plan: dict[str, Any]) -> bool:
    subject = research_plan.get("subject") if isinstance(research_plan.get("subject"), dict) else {}
    subject_name = _normalize_subject_title(str(subject.get("name") or ""))
    title = _normalize_subject_title(str(source.get("title") or ""))
    if not subject_name or not title or title == subject_name or subject_name in title:
        return False
    if _looks_like_short_cjk_name(subject_name) and _looks_like_short_cjk_name(title):
        return True
    return False


def _looks_like_short_cjk_name(value: str) -> bool:
    text = re.sub(r"\s+", "", value or "")
    return bool(2 <= len(text) <= 6 and re.fullmatch(r"[\u4e00-\u9fff]+", text))


def _source_is_adjacent_for_definition_claim(research_plan: dict[str, Any], source: dict[str, Any]) -> bool:
    if not _plan_is_definition_like(research_plan):
        return False
    title = re.sub(r"\s+", " ", str(source.get("title") or "")).strip()
    if not title or title.startswith("联网搜索结果"):
        return False
    subject = research_plan.get("subject") if isinstance(research_plan.get("subject"), dict) else {}
    names = [str(subject.get("name") or ""), *[str(item or "") for item in subject.get("aliases") or []]]
    for name in names:
        if _title_is_adjacent_to_subject(title, name):
            return True
    return False


def _sort_sources_by_subject_title_priority(
    sources: list[dict[str, Any]],
    subject: dict[str, Any],
) -> list[dict[str, Any]]:
    if not sources:
        return sources
    return sorted(
        sources,
        key=lambda source: (
            _subject_title_priority(source, subject),
            float(source.get("rank_score") or 0),
        ),
        reverse=True,
    )


def _subject_title_priority(source: dict[str, Any], subject: dict[str, Any]) -> int:
    title = str(source.get("title") or "").strip()
    if not title or title.startswith("联网搜索结果"):
        return 0
    normalized_title = _normalize_subject_title(title).lower()
    names = [str(subject.get("name") or ""), *[str(item or "") for item in subject.get("aliases") or []]]
    for name in names:
        normalized_name = _normalize_subject_title(name).lower()
        if not normalized_name:
            continue
        if normalized_title == normalized_name:
            return 4
        if normalized_title.startswith(f"{normalized_name} "):
            return 3
        if normalized_name in normalized_title:
            return 2
    return 0


def _plan_is_definition_like(research_plan: dict[str, Any]) -> bool:
    subject = research_plan.get("subject") if isinstance(research_plan.get("subject"), dict) else {}
    type_hint = str(subject.get("type_hint") or "").strip().lower()
    if type_hint in {"finance_term", "financial_term", "concept", "term", "术语", "概念", "金融术语", "金融概念"}:
        return True
    haystack = " ".join(
        [
            str(research_plan.get("intent_label") or ""),
            " ".join(_string_list(research_plan.get("answer_claims_needed"))),
        ]
    ).lower()
    return any(marker in haystack for marker in ("definition", "define", "overview", "定义", "是什么", "什么意思"))


def _title_is_adjacent_to_subject(title: str, subject_name: str) -> bool:
    clean_subject = _normalize_subject_title(subject_name)
    clean_title = _normalize_subject_title(title)
    if not clean_subject or not clean_title or clean_title == clean_subject:
        return False
    definition_markers = {"definition", "defined", "meaning", "overview", "what is", "是什么", "什么意思", "定义", "简介"}
    title_lower = clean_title.lower()
    if any(marker in title_lower for marker in definition_markers):
        return False
    if _contains_cjk(clean_subject):
        return clean_subject in clean_title
    subject_tokens = clean_subject.lower().split()
    title_tokens = clean_title.lower().split()
    if not subject_tokens:
        return False
    if title_tokens[: len(subject_tokens)] == subject_tokens:
        extra = title_tokens[len(subject_tokens):]
        return bool(extra)
    return False


def _normalize_subject_title(value: str) -> str:
    text = re.sub(r"\([^)]*\)", " ", str(value or ""))
    text = re.sub(r"[-|｜].*$", " ", text)
    text = re.sub(r"[《》“”\"'‘’]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _classify_ranked_sources(
    query: str,
    ranked: list[dict[str, Any]],
    *,
    source_preferences: dict[str, Any],
    subject: dict[str, Any] | None = None,
    evidence_contract: dict[str, Any] | None = None,
    adopted_sources: list[dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    adopted_urls = {str(source.get("url") or "") for source in (adopted_sources or []) if source.get("url")}
    candidate_sources: list[dict[str, Any]] = []

    for source in _web_sources_from_ranked(ranked, {}):
        url = source.get("url") or ""
        if url in adopted_urls:
            role = "evidence"
        elif _is_generic_homepage_url(url):
            role = "rejected"
        else:
            role = "candidate"
        card = {
            **source,
            "source_role": role,
            "source_score": source.get("rank_score", 0),
            "quality_reason": role,
        }
        candidate_sources.append(card)

    rejected_sources = [card for card in candidate_sources if card["source_role"] == "rejected"]
    return {
        "candidate_sources": candidate_sources,
        "clue_sources": [],
        "rejected_sources": rejected_sources,
    }


def _annotate_evidence_sources(
    query: str,
    sources: list[dict[str, Any]],
    *,
    research_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    plan = research_plan or {}
    return [
        {
            **source,
            "source_role": "evidence",
            "source_score": source.get("rank_score", 0),
            "quality_reason": "adopted_evidence",
            "entity_match": _source_matches_subject(query, source),
            "quality_tags": _unique(
                [
                    *(source.get("quality_tags") or []),
                    *(
                        ["adjacent_subject_title"]
                        if _source_is_adjacent_for_definition_claim(plan, source)
                        else []
                    ),
                    *(
                        ["subject_title_conflict"]
                        if _source_title_conflicts_with_subject(source, plan)
                        else []
                    ),
                ]
            ),
        }
        for source in sources
    ]


def _source_matches_subject(query: str, source: dict[str, Any]) -> bool:
    required_terms = _subject_terms(query)
    if not required_terms:
        return True
    return any(_source_contains_term(source, term) for term in required_terms)


def _apply_critic_rejections_to_classified(
    classified: dict[str, list[dict[str, Any]]],
    critic_rejected_sources: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    if not critic_rejected_sources:
        return classified
    non_evidence_by_url = {
        str(source.get("url") or ""): source
        for source in critic_rejected_sources
        if source.get("url")
    }
    candidate_sources = []
    for source in classified.get("candidate_sources") or []:
        non_evidence = non_evidence_by_url.get(str(source.get("url") or ""))
        candidate_sources.append({**source, **non_evidence} if non_evidence else source)
    candidate_urls = {str(source.get("url") or "") for source in candidate_sources if source.get("url")}
    clue_sources = [
        source
        for source in candidate_sources
        if source.get("source_role") == "clue"
    ]
    rejected_sources = [
        source
        for source in candidate_sources
        if source.get("source_role") == "rejected"
    ]
    seen_clue = {str(source.get("url") or "") for source in clue_sources}
    seen_rejected = {str(source.get("url") or "") for source in rejected_sources}
    for source in critic_rejected_sources:
        url = str(source.get("url") or "")
        if url in candidate_urls:
            continue
        role = str(source.get("source_role") or "")
        if role == "clue":
            if url and url not in seen_clue:
                clue_sources.append(source)
                seen_clue.add(url)
        elif url and url not in seen_rejected:
            rejected_sources.append(source)
            seen_rejected.add(url)
    clue_sources = [
        source
        for source in clue_sources
        if str(source.get("url") or "") not in seen_rejected
    ]
    return {
        "candidate_sources": candidate_sources,
        "clue_sources": clue_sources,
        "rejected_sources": rejected_sources,
    }
