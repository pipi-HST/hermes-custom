"""Search collection, ranking, quality evaluation, and extraction."""
from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from agents.source_registry import get_registry
from agents.research.types import (
    MAX_DYNAMIC_EXTRACT_URLS,
    MAX_EXTRACT_URLS,
    MAX_WEB_CANDIDATES,
    MAX_WEB_QUERIES,
    MAX_WEB_RESULTS,
    DIRECT_PROBE_MIN_REMAINING_SECONDS,
    DIRECT_PROBE_TIMEOUT_SECONDS,
    ResearchExtractAdapter,
    _RESEARCH_DEADLINE,
)
from agents.research.utils import (
    _clean_web_url,
    _clip_text,
    _current_date_for_prompt,
    _dedupe_web_results,
    _domain,
    _domain_in_list,
    _domain_matches_required_entity,
    _parse_web_extract,
    _parse_web_extract_failures,
    _sanitize_evidence_contract,
    _sanitize_claim_requirements,
    _sanitize_source_preferences,
    _string_list,
    _unique,
    _unique_queries,
)


def _normalize_mode(mode: str) -> str:
    """Normalize research mode to valid value."""
    from agents.research.types import VALID_MODES

    value = (mode or "local_web").strip()
    return value if value in VALID_MODES else "local_web"


def _no_web_sources_warning(*, use_local: bool, use_article: bool) -> str:
    """Generate warning message when no web sources found."""
    if use_local or use_article:
        return "当前没有可用的联网搜索结果，已基于可用的本地知识库和当前文章回答。"
    return "当前没有足够可靠的联网搜索结果，未基于二手或不可验证来源给出确定结论。"


def should_search_web(query: str, mode: str = "local_web") -> bool:
    """Determine if web search should be performed based on mode."""
    normalized_mode = _normalize_mode(mode)
    if normalized_mode == "web_only":
        return True
    if normalized_mode in {"local_only", "current_article_only"}:
        return False
    return True


def _same_or_parent_domain(domain: str, expected: str) -> bool:
    """Check if domain matches expected domain or is subdomain."""
    left = str(domain or "").lower().removeprefix("www.")
    right = str(expected or "").lower().removeprefix("www.")
    return bool(left and right and (left == right or left.endswith(f".{right}") or right.endswith(f".{left}")))


def _is_language_dictionary_source(domain: str) -> bool:
    """Check if domain is a language dictionary source."""
    return get_registry().is_language_dictionary(str(domain or ""))


def _source_identity_quality_tags(item: dict[str, Any], domain: str) -> list[str]:
    """Check if source content matches expected domain identity."""
    text = " ".join(str(item.get(key) or "") for key in ("title", "description", "snippet", "summary", "content"))
    mentioned_domains = {_domain(match) for match in _mentioned_urls(text)}
    mentioned_domains.discard("")
    for mentioned in mentioned_domains:
        if not _same_or_parent_domain(domain, mentioned):
            return ["source_identity_mismatch"]
    lowered = text.lower()
    for alias, domains in get_registry().identity_aliases.items():
        if alias in lowered and domain and not any(_same_or_parent_domain(domain, expected) for expected in domains):
            return ["source_identity_mismatch"]
    return []


def _mentioned_urls(text: str) -> list[str]:
    """Extract URLs from text."""
    urls: list[str] = []
    for match in re.findall(r"https?://[^\s，。；,;()（）\[\]【】「」『』""\"'`<>]+", text or ""):
        cleaned = _clean_mentioned_url(match)
        if cleaned:
            urls.append(cleaned)
    return _unique_queries(urls)[:8]


def _clean_mentioned_url(url: str) -> str:
    """Clean URL extracted from text."""
    cleaned = str(url or "").strip()
    cleaned = re.split(r"[，。；,;()（）\[\]【】「」『』""\"'`<>]", cleaned, maxsplit=1)[0]
    return cleaned.rstrip(".。；;、,，")


def _html_text(value: str) -> str:
    """Strip HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return re.sub(r"\s+", " ", text).strip()


def _direct_probe_budget_available() -> bool:
    """Check if budget available for direct URL probe."""
    remaining = _remaining_research_budget()
    return remaining is None or remaining >= max(1.0, DIRECT_PROBE_MIN_REMAINING_SECONDS)


def _direct_probe_timeout() -> float:
    """Get timeout for direct URL probe."""
    remaining = _remaining_research_budget()
    if remaining is None:
        return max(1.0, DIRECT_PROBE_TIMEOUT_SECONDS)
    spare = remaining - max(1.0, DIRECT_PROBE_MIN_REMAINING_SECONDS)
    return max(1.0, min(DIRECT_PROBE_TIMEOUT_SECONDS, spare))


def _remaining_research_budget() -> float | None:
    """Get remaining research budget in seconds."""
    import time

    deadline = _RESEARCH_DEADLINE.get()
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _fetch_url_summary(url: str) -> dict[str, str] | None:
    """Fetch URL and extract title and description."""
    if not _direct_probe_budget_available():
        return None
    try:
        from agents.extractors.http_safety import fetch_public_text

        _, text = fetch_public_text(
            url,
            timeout=_direct_probe_timeout(),
            max_bytes=1_000_000,
            headers={"User-Agent": "HermesResearchBot/1.0"},
        )
    except Exception:
        return None
    text = text or ""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    description_match = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        text,
        re.I | re.S,
    )
    title = _html_text(title_match.group(1)) if title_match else url
    description = _html_text(description_match.group(1)) if description_match else ""
    if not description:
        description = _html_text(re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S))[:800]
    return {
        "title": title or url,
        "url": url,
        "description": description,
    }


def _independent_source_count(sources: list[dict[str, Any]]) -> int:
    """Count independent sources by domain/URL."""
    independent = {
        str(source.get("domain") or _domain(source.get("url") or "") or source.get("url") or source.get("id") or "")
        for source in sources
        if isinstance(source, dict)
    }
    independent.discard("")
    return len(independent)


def _minimum_reliable_source_count(evidence_contract: dict[str, Any]) -> int:
    """Get minimum reliable source count from evidence contract."""
    return int(_sanitize_evidence_contract(evidence_contract or {})["minimum_independent_sources"])


def _empty_search_quality(reason: str) -> dict[str, Any]:
    """Create empty search quality result."""
    return {
        "passed": False,
        "score": 0.0,
        "reason": reason,
        "matched_terms": [],
        "core_terms": [],
        "required_terms": [],
        "trusted_sources": 0,
        "preferred_sources": 0,
        "evidence_status": "insufficient",
        "rewrite_attempted": False,
        "rewritten_queries": [],
    }


def _should_let_critic_judge_quality_failure(
    quality: dict[str, Any],
    research_plan: dict[str, Any],
) -> bool:
    """Determine if critic should judge quality failure."""
    reason = quality.get("reason")
    if reason in {"no_results", "not_searched"}:
        return False
    return True


def _is_low_quality_source(source: dict[str, Any]) -> bool:
    """Check if source is low quality."""
    tags = set(source.get("quality_tags") or [])
    return bool({"low_quality_domain", "low_quality_format"} & tags)


def _is_generic_homepage_source(source: dict[str, Any]) -> bool:
    """Check if source is a generic homepage."""
    return "generic_homepage" in set(source.get("quality_tags") or [])


def _is_generic_homepage_url(url: str) -> bool:
    """Check if URL is a generic homepage."""
    try:
        parsed = urlparse(url or "")
    except Exception:
        return False
    path = (parsed.path or "").strip("/")
    return bool(parsed.netloc) and not path


def _is_weak_secondary_source(source: dict[str, Any]) -> bool:
    """Check if source is a weak secondary source."""
    return "weak_secondary_source" in set(source.get("quality_tags") or [])


def _min_recall_candidates_for_plan(research_plan: dict[str, Any]) -> int:
    """Get minimum recall candidates for research plan."""
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    minimum_sources = int(contract.get("minimum_independent_sources") or 1)
    if research_plan.get("risk_level") == "high" or research_plan.get("freshness_sensitive"):
        return max(3, minimum_sources + 1)
    if research_plan.get("answer_depth") == "detailed":
        return max(5, minimum_sources + 2)
    if minimum_sources > 1:
        return max(3, minimum_sources + 1)
    return 1


def _min_recall_domains_for_plan(research_plan: dict[str, Any]) -> int:
    """Get minimum recall domains for research plan."""
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    minimum_sources = int(contract.get("minimum_independent_sources") or 1)
    if research_plan.get("risk_level") == "high" or research_plan.get("freshness_sensitive"):
        return max(2, min(minimum_sources, 3))
    if research_plan.get("answer_depth") == "detailed" or minimum_sources > 1:
        return max(2, min(minimum_sources, 3))
    return 1


def _allows_interpretive_secondary_recall(research_plan: dict[str, Any]) -> bool:
    """Check if interpretive secondary recall is allowed."""
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    if contract.get("prefer_authoritative_sources"):
        return False
    return _allows_secondary_evidence_candidates(research_plan)


def _allows_secondary_evidence_candidates(research_plan: dict[str, Any]) -> bool:
    """Check if secondary evidence candidates are allowed."""
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    claim_requirements = _sanitize_claim_requirements(research_plan.get("claim_requirements"))
    if research_plan.get("risk_level") == "high" or research_plan.get("freshness_sensitive"):
        return False
    if contract.get("must_use_primary_source"):
        return False
    if any(
        requirement.get("required_authority") in {"primary_or_index", "primary_only"}
        for requirement in claim_requirements.values()
    ):
        return False
    return True


def _retrieval_repair_reason(
    *,
    search_quality: dict[str, Any],
    candidate_count: int,
    independent_domain_count: int,
    generic_count: int,
    weak_count: int,
    low_quality_count: int,
    min_candidates: int,
    min_domains: int,
    minimum_sources: int,
    weak_or_generic_repair: bool,
) -> str:
    """Determine retrieval repair reason."""
    if not bool(search_quality.get("passed")):
        return str(search_quality.get("reason") or "search_quality_failed")
    if candidate_count < min_candidates:
        return "too_few_candidates"
    if independent_domain_count < min_domains:
        return "too_few_independent_domains"
    if minimum_sources > 1 and independent_domain_count < minimum_sources:
        return "too_few_required_independent_sources"
    if weak_or_generic_repair:
        return "too_many_weak_or_generic_results"
    return ""


def _preferred_query_domains(preferences: dict[str, Any]) -> list[str]:
    """Get preferred query domains from preferences."""
    registry = get_registry()
    domains = list(preferences.get("preferred_domains") or [])
    for kind in preferences.get("preferred_source_kinds") or []:
        domains.extend(registry.preferred_kind_domains(kind))
    return _unique_queries(domains)[:8]


def _results_include_preferred_domain(results: list[dict[str, Any]], source_preferences: dict[str, Any]) -> bool:
    """Check if results include preferred domain."""
    preferences = _sanitize_source_preferences(source_preferences or {})
    preferred_domains = preferences.get("preferred_domains") or []
    if not preferred_domains:
        return True
    return any(
        _domain_in_list(str(item.get("domain") or _domain(item.get("url") or "")), preferred_domains)
        for item in results
        if isinstance(item, dict)
    )


def _domain_matches_subject(domain: str, subject: dict[str, Any]) -> bool:
    """Check if domain matches subject terms."""
    terms: list[str] = []
    for value in [subject.get("name") or "", *(subject.get("aliases") or [])]:
        normalized = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
        if len(normalized) >= 3:
            terms.append(normalized)
    return _domain_matches_required_entity(domain, _unique_queries(terms))


def _source_matches_kind(domain: str, url: str, kind: str) -> bool:
    """Check if source matches specified kind."""
    registry = get_registry()
    normalized_kind = kind.strip().lower()
    path = urlparse(url or "").path.lower()
    if normalized_kind in {"official", "primary_source"}:
        return bool(registry.official_score(domain))
    if normalized_kind in {"encyclopedia", "百科"}:
        return registry.is_encyclopedia(domain)
    if normalized_kind in {"profile", "bio", "biography", "人物简介"}:
        return bool(registry.profile_score(domain))
    if normalized_kind in {"interview", "访谈", "采访"}:
        return any(part in path for part in ("/interview", "/video/interview", "fangtan", "zhuanfang"))
    if normalized_kind in {"documentation", "docs"}:
        return "/docs" in path or "/documentation" in path
    if normalized_kind in {"finance_glossary", "glossary", "dictionary", "术语表"}:
        return bool(registry.glossary_score(domain)) or "glossary" in path or "dictionary" in path
    if normalized_kind in {"educational", "education", "academic", "教材"}:
        return domain.endswith(".edu") or domain.endswith(".edu.cn") or "/learn" in path or "/education" in path
    if normalized_kind in {"news", "media", "reputable_media", "主流媒体"}:
        if registry.low_quality_score(domain):
            return False
        return bool(registry.news_score(domain))
    return False


def _mentioned_url_matches_plan(
    url: str,
    domain: str,
    subject: dict[str, Any],
    preferences: dict[str, Any],
) -> bool:
    """Check if mentioned URL matches research plan."""
    preferred_domains = preferences.get("preferred_domains") or []
    if preferred_domains:
        return _domain_in_list(domain, preferred_domains)
    kinds = {str(kind).strip().lower() for kind in preferences.get("preferred_source_kinds") or []}
    if any(_source_matches_kind(domain, url, kind) for kind in kinds):
        return True
    if {"official", "primary_source"} & kinds and _domain_matches_subject(domain, subject):
        return True
    if {"documentation", "docs"} & kinds and _source_matches_kind(domain, url, "documentation"):
        return True
    return _domain_matches_subject(domain, subject)


def _results_include_source_kind(
    results: list[dict[str, Any]],
    preferences: dict[str, Any],
    *,
    min_independent_domains: int = 1,
) -> bool:
    """Check if results include specified source kind."""
    kinds = [str(kind).strip().lower() for kind in preferences.get("preferred_source_kinds") or []]
    if not kinds:
        return True
    domains: set[str] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        domain = str(item.get("domain") or _domain(url))
        if not domain:
            continue
        if any(_source_matches_kind(domain, url, kind) for kind in kinds):
            domains.add(domain.removeprefix("www."))
    return len(domains) >= max(1, int(min_independent_domains or 1))


def _subject_probe_slug_candidates(subject: dict[str, Any]) -> list[str]:
    """Generate slug candidates from subject."""
    values: list[str] = []
    aliases = [str(item or "").strip() for item in (subject or {}).get("aliases") or []]
    name = str((subject or {}).get("name") or "").strip()
    values.extend([alias for alias in aliases if re.fullmatch(r"[A-Za-z0-9]{2,12}", alias)])
    if name:
        values.append(name)
    values.extend(aliases)
    slugs: list[str] = []
    for value in values:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 &+.-]{1,80}", value):
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        if slug:
            slugs.append(slug)
    return _unique_queries(slugs)[:4]


def _query_slug_candidates(query: str, domain: str) -> list[str]:
    """Generate slug candidates from query and domain."""
    domain_labels = {
        label
        for label in re.split(r"[^a-z0-9]+", domain.lower())
        if label and label not in {"www", "com", "org", "net", "io", "ai", "docs"}
    }
    stop = {
        "what",
        "official",
        "website",
        "documentation",
        "docs",
        "about",
        "overview",
        "company",
        "tool",
        "product",
        "api",
        "官网",
        "官方",
        "文档",
        "是什么",
        "什么意思",
    }
    tokens: list[str] = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{1,}", query or ""):
        value = token.lower().strip("-_")
        if not value or value in stop or value in domain_labels:
            continue
        tokens.append(value)
    candidates: list[str] = []
    if tokens:
        candidates.append("-".join(tokens[:2]))
        candidates.append(tokens[0])
    return _unique_queries([item for item in candidates if len(item) >= 3])[:2]


def _preferred_domain_probe_urls(query: str, domain: str, *, subject: dict[str, Any] | None = None) -> list[str]:
    """Generate probe URLs for preferred domain."""
    base = f"https://{domain}"
    landing_urls = [
        f"{base}/",
        f"{base}/about/",
        f"{base}/company/",
        f"{base}/docs/",
        f"{base}/documentation/",
    ]
    slug_urls: list[str] = []
    slug_candidates = _unique_queries(
        [
            *_subject_probe_slug_candidates(subject or {}),
            *_query_slug_candidates(query, domain),
        ]
    )
    for slug in slug_candidates:
        slug_urls.extend(
            [
                f"{base}/{slug}",
                f"{base}/{slug}/",
                f"{base}/{slug}/marketcap/",
                f"{base}/docs/{slug}",
                f"{base}/docs/{slug}/overview",
                f"{base}/en/docs/{slug}",
                f"{base}/en/docs/{slug}/overview",
            ]
        )
    slug_urls = _unique_queries(slug_urls)[:10]
    urls = [*slug_urls, *landing_urls] if slug_urls else landing_urls
    return _unique_queries(urls)[:14]


def _subject_slug_candidates_for_direct_sources(query: str, subject: dict[str, Any]) -> list[str]:
    """Generate slug candidates for direct source lookup."""
    candidates: list[str] = []
    for value in [subject.get("name") or "", *(subject.get("aliases") or [])]:
        text = str(value or "").strip()
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9 -]{1,40}", text):
            candidates.append(re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-"))
    for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,40}", query or ""):
        lowered = token.lower().strip("-")
        if lowered not in {"what", "definition", "finance", "official"}:
            candidates.append(lowered)
    return _unique_queries([item for item in candidates if item])[:3]


def _finance_glossary_probe_urls(query: str, subject: dict[str, Any]) -> list[str]:
    """Generate probe URLs for finance glossary sites."""
    urls: list[str] = []
    for slug in _subject_slug_candidates_for_direct_sources(query, subject):
        first = slug[0]
        urls.extend(
            [
                f"https://www.investopedia.com/terms/{first}/{slug}.asp",
                f"https://corporatefinanceinstitute.com/resources/career-map/sell-side/capital-markets/{slug}-vol/",
                f"https://corporatefinanceinstitute.com/resources/capital-markets/{slug}-vol/",
            ]
        )
    return _unique_queries(urls)[:8]


def _missing_required_preferred_source(
    research_plan: dict[str, Any],
    sources: list[dict[str, Any]],
) -> bool:
    """Check if required preferred source is missing."""
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    if not contract.get("must_use_primary_source"):
        return False
    for source in sources:
        authority = str(source.get("source_authority") or "unknown")
        if (
            authority in {"primary", "index_database", "official"}
            and source.get("extract_status") in {"full", "summary"}
            and (source.get("excerpt") or source.get("description"))
        ):
            return False
    return True


def _missing_minimum_reliable_sources(
    research_plan: dict[str, Any],
    sources: list[dict[str, Any]],
) -> bool:
    """Check if minimum reliable sources is missing."""
    minimum = _minimum_reliable_source_count(research_plan.get("evidence_contract") or {})
    if minimum <= 1:
        return False
    return _independent_source_count(sources) < minimum


def _missing_preferred_domain_adoption(
    research_plan: dict[str, Any],
    sources: list[dict[str, Any]],
) -> bool:
    """Check if preferred domain adoption is missing."""
    preferences = _sanitize_source_preferences(research_plan.get("source_preferences") or {})
    preferred_domains = preferences.get("preferred_domains") or []
    if not preferred_domains:
        return False
    return not any(
        _domain_in_list(str(source.get("domain") or _domain(source.get("url") or "")), preferred_domains)
        for source in sources
        if isinstance(source, dict)
    )


def _requires_preferred_source_quality(
    preferences: dict[str, Any],
    evidence_contract: dict[str, Any] | None = None,
) -> bool:
    """Check if preferred source quality is required."""
    contract = _sanitize_evidence_contract(evidence_contract or {})
    if contract.get("must_use_primary_source"):
        return bool(preferences.get("prefer_primary_sources") or preferences.get("preferred_domains"))
    return False


def _source_satisfies_preferences(source: dict[str, Any], preferences: dict[str, Any]) -> bool:
    """Check if source satisfies preferences."""
    if _is_low_quality_source(source) or _is_weak_secondary_source(source) or _is_generic_homepage_source(source):
        return False
    tags = set(source.get("quality_tags") or [])
    if "planner_preferred_domain" in tags or "planner_primary_source" in tags:
        return True
    if preferences.get("preferred_domains"):
        return False
    if any(str(tag).startswith("planner_source_kind:") for tag in tags):
        return True
    return bool(source.get("trusted_source"))


def _normalized_evidence_claim_ids(claims: list[str]) -> list[str]:
    """Normalize evidence claim IDs."""
    normalized: list[str] = []
    for claim in claims:
        value = str(claim or "").strip()
        if not value:
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_:-]{0,79}", value):
            normalized.append(value)
        else:
            normalized.append("answer_user_question")
    return _unique_queries(normalized)[:8]


def _max_extract_urls_for_plan(research_plan: dict[str, Any]) -> int:
    """Get max extract URLs for research plan."""
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    minimum = int(contract.get("minimum_independent_sources") or 1)
    if research_plan.get("risk_level") == "high" or research_plan.get("freshness_sensitive"):
        return min(max(4, minimum + 2), 5)
    if research_plan.get("answer_depth") == "detailed":
        return min(max(5, minimum + 3), MAX_DYNAMIC_EXTRACT_URLS)
    if minimum >= 2:
        return min(max(4, minimum + 2), 5)
    return min(max(4, minimum + 2), MAX_DYNAMIC_EXTRACT_URLS)


def _current_year() -> int:
    """Get current year (supports env override via HERMES_CURRENT_DATE)."""
    try:
        return int(_current_date_for_prompt()[:4])
    except Exception:
        return 0


def _web_sources_from_ranked(
    ranked: list[dict[str, Any]],
    extracted_by_url: dict[str, str],
    *,
    failed_urls: set[str] | None = None,
    hard_failed_urls: set[str] | None = None,
    limit: int = MAX_WEB_RESULTS,
    allow_failed_summary: bool = False,
    allow_failed_title_summary: bool = False,
) -> list[dict[str, Any]]:
    """Convert ranked results to web sources with excerpts."""
    sources: list[dict[str, Any]] = []
    failed_urls = failed_urls or set()
    hard_failed_urls = hard_failed_urls or set()
    for item in ranked[: max(1, min(int(limit or MAX_WEB_RESULTS), MAX_DYNAMIC_EXTRACT_URLS))]:
        url = item.get("url", "")
        extract_failed = url in failed_urls
        hard_extract_failed = url in hard_failed_urls
        is_direct_probe = str(item.get("query") or "").startswith("direct:")
        fallback_summary = str(item.get("description") or "").strip() if (not hard_extract_failed or allow_failed_summary or is_direct_probe) else ""
        if not fallback_summary and extract_failed and allow_failed_title_summary:
            fallback_summary = str(item.get("title") or "").strip()
        failed_summary_fallback = bool(extract_failed and fallback_summary and (allow_failed_summary or allow_failed_title_summary))
        excerpt = (
            extracted_by_url.get(url)
            or (fallback_summary if (not extract_failed or is_direct_probe or fallback_summary) else "")
            or ""
        ).strip()
        if extract_failed and not excerpt:
            continue
        quality_tags = list(item.get("quality_tags") or [])
        if failed_summary_fallback:
            quality_tags.append("extract_failed_summary_fallback")
        ref_index = len(sources) + 1
        sources.append(
            {
                "ref": f"W{ref_index}",
                "type": "web",
                "id": url or f"web-{ref_index}",
                "title": item.get("title") or url or f"网页来源 {ref_index}",
                "url": url,
                "domain": item.get("domain") or _domain(url),
                "domain_roles": item.get("domain_roles") or [],
                "domain_scores": item.get("domain_scores") or {},
                "query": item.get("query") or "",
                "search_backend": item.get("search_backend") or "",
                "fallback_candidate": bool(item.get("fallback_candidate")),
                "excerpt": excerpt[:1800],
                "description": item.get("description") or "",
                "extract_status": (
                    "full"
                    if extracted_by_url.get(url)
                    else ("failed" if extract_failed and is_direct_probe and not failed_summary_fallback else ("summary" if excerpt else "failed"))
                ),
                "rank_score": item.get("rank_score", 0),
                "quality_tags": _unique(quality_tags),
                "matched_terms": item.get("matched_terms") or [],
            }
        )
    return sources


def extract_ranked_web_sources(
    ranked: list[dict[str, Any]],
    *,
    extract_adapter: ResearchExtractAdapter,
    warnings: list[str],
    max_urls: int = MAX_EXTRACT_URLS,
    allow_failed_summary: bool = False,
    allow_failed_title_summary: bool = False,
) -> list[dict[str, Any]]:
    """Extract web sources from ranked results."""
    limit = max(1, min(int(max_urls or MAX_EXTRACT_URLS), MAX_DYNAMIC_EXTRACT_URLS))
    urls = [item["url"] for item in ranked[:limit] if item.get("url")]
    extracted_by_url: dict[str, str] = {}
    failed_urls: set[str] = set()
    hard_failed_urls: set[str] = set()
    if urls:
        try:
            raw = extract_adapter(urls)
            extracted_by_url = _parse_web_extract(raw)
            hard_failed_urls = _parse_web_extract_failures(raw)
            failed_urls = set(hard_failed_urls)
            failed_urls.update(url for url in urls if url not in extracted_by_url)
        except Exception as exc:
            failed_urls = set(urls)
            hard_failed_urls = set(urls)
            warnings.append(f"网页正文抽取失败，已仅使用搜索摘要：{exc}")
    return _web_sources_from_ranked(
        ranked,
        extracted_by_url,
        failed_urls=failed_urls,
        hard_failed_urls=hard_failed_urls,
        limit=max(MAX_WEB_RESULTS, limit),
        allow_failed_summary=allow_failed_summary,
        allow_failed_title_summary=allow_failed_title_summary,
    )


async def extract_ranked_web_sources_async(
    ranked: list[dict[str, Any]],
    *,
    extract_adapter: ResearchExtractAdapter,
    warnings: list[str],
    max_urls: int = MAX_EXTRACT_URLS,
    allow_failed_summary: bool = False,
    allow_failed_title_summary: bool = False,
) -> list[dict[str, Any]]:
    """Extract web sources from ranked results (async version)."""
    limit = max(1, min(int(max_urls or MAX_EXTRACT_URLS), MAX_DYNAMIC_EXTRACT_URLS))
    urls = [item["url"] for item in ranked[:limit] if item.get("url")]
    extracted_by_url: dict[str, str] = {}
    failed_urls: set[str] = set()
    hard_failed_urls: set[str] = set()
    if urls:
        try:
            raw = extract_adapter(urls)
            if asyncio.iscoroutine(raw):
                raw = await raw
            extracted_by_url = _parse_web_extract(raw)
            hard_failed_urls = _parse_web_extract_failures(raw)
            failed_urls = set(hard_failed_urls)
            failed_urls.update(url for url in urls if url not in extracted_by_url)
        except Exception as exc:
            failed_urls = set(urls)
            hard_failed_urls = set(urls)
            warnings.append(f"网页正文抽取失败，已仅使用搜索摘要：{exc}")
    return _web_sources_from_ranked(
        ranked,
        extracted_by_url,
        failed_urls=failed_urls,
        hard_failed_urls=hard_failed_urls,
        limit=max(MAX_WEB_RESULTS, limit),
        allow_failed_summary=allow_failed_summary,
        allow_failed_title_summary=allow_failed_title_summary,
    )
