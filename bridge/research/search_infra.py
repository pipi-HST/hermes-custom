"""Search infrastructure: web search collection, ranking, quality evaluation, and query rewriting."""
from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from typing import Any

from agents.source_registry import get_registry

from bridge.research.types import (
    ResearchSearchAdapter,
    ResearchEventCallback,
    ResearchQueryPlannerAdapter,
    MAX_WEB_RESULTS,
    MAX_WEB_QUERIES,
    MAX_WEB_CANDIDATES,
)
from bridge.research.utils import (
    _clean_web_url,
    _clip_text,
    _contains_cjk,
    _dedupe_web_results,
    _domain,
    _domain_in_list,
    _emit_research_event_async,
    _parse_web_search,
    _sanitize_evidence_contract,
    _sanitize_source_preferences,
    _unique,
    _unique_queries,
    _web_search_stage_timeout,
)
from bridge.research.search import (
    _allows_interpretive_secondary_recall,
    _direct_probe_budget_available,
    _empty_search_quality,
    _fetch_url_summary,
    _finance_glossary_probe_urls,
    _is_generic_homepage_source,
    _is_generic_homepage_url,
    _is_language_dictionary_source,
    _is_low_quality_source,
    _is_weak_secondary_source,
    _mentioned_url_matches_plan,
    _mentioned_urls,
    _min_recall_candidates_for_plan,
    _min_recall_domains_for_plan,
    _preferred_domain_probe_urls,
    _preferred_query_domains,
    _results_include_source_kind,
    _retrieval_repair_reason,
    _same_or_parent_domain,
    _source_identity_quality_tags,
    _source_satisfies_preferences,
)
from bridge.research.planner import (
    _limited_search_queries,
    _normalized_search_query_limit,
    _plan_requires_full_search_budget,
    _planned_research_plan,
    _subject_terms,
)
from bridge.research.synthesis import _trace_sources


def collect_web_search_results(
    queries: list[str],
    *,
    search_adapter: ResearchSearchAdapter,
    warnings: list[str],
    backend_label: str = "",
    max_queries: int | None = None,
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    errors: list[str] = []
    search_queries = _limited_search_queries(queries, max_queries)
    if not search_queries:
        return []
    timeout_seconds = _web_search_stage_timeout()
    if timeout_seconds < 1.0:
        warnings.append("联网搜索预算已耗尽，已停止继续请求搜索后端。")
        return []
    executor = ThreadPoolExecutor(max_workers=min(len(search_queries), MAX_WEB_QUERIES))
    futures = {
        executor.submit(_search_query_candidates, search_query, search_adapter, backend_label): search_query
        for search_query in search_queries
    }
    try:
        for future in as_completed(futures, timeout=timeout_seconds):
            search_query = futures[future]
            try:
                candidates.extend(future.result())
            except Exception as exc:
                errors.append(f"{search_query}: {_friendly_search_error(exc)}")
    except FuturesTimeoutError:
        for future, search_query in futures.items():
            if not future.done():
                errors.append(f"{search_query}: 搜索超时")
                future.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    if errors and not candidates:
        warnings.append(f"联网搜索失败：{'; '.join(errors[:2])}")
    elif errors:
        warnings.append(f"部分联网搜索失败，已使用可用结果：{'; '.join(errors[:2])}")
    return _dedupe_web_results(candidates)[:MAX_WEB_CANDIDATES]


def _search_query_candidates(
    search_query: str,
    search_adapter: ResearchSearchAdapter,
    backend_label: str = "",
) -> list[dict[str, str]]:
    raw = search_adapter(search_query, MAX_WEB_RESULTS)
    return [
        {**item, "query": search_query, "search_backend": item.get("search_backend") or backend_label}
        for item in _parse_web_search(raw)
    ]


def _friendly_search_error(exc: Exception) -> str:
    message = str(exc)
    if "No web search provider configured" in message:
        return "当前没有可用的联网搜索后端；请在 Hermes tools 配置 web backend，或启用 ddgs/Exa/Tavily/Brave 等搜索 provider"
    if "ddgs package is not installed" in message:
        return "当前环境缺少 ddgs 搜索依赖，请在 Hermes 虚拟环境中安装或切换其他 web backend"
    return message


async def collect_web_search_results_async(
    queries: list[str],
    *,
    search_adapter: ResearchSearchAdapter,
    warnings: list[str],
    event_callback: ResearchEventCallback | None = None,
    backend_label: str = "",
    max_queries: int | None = None,
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    errors: list[str] = []
    search_queries = _limited_search_queries(queries, max_queries)
    if not search_queries:
        return []
    timeout_seconds = _web_search_stage_timeout()
    if timeout_seconds < 1.0:
        warnings.append("联网搜索预算已耗尽，已停止继续请求搜索后端。")
        return []
    for search_query in search_queries:
        await _emit_research_event_async(event_callback, "web_query_start", query=search_query)
    tasks = {
        asyncio.create_task(asyncio.to_thread(_search_query_candidates, search_query, search_adapter, backend_label)): search_query
        for search_query in search_queries
    }
    pending = set(tasks.keys())
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while pending:
        timeout = max(0.0, deadline - asyncio.get_running_loop().time())
        if timeout <= 0:
            break
        done, pending = await asyncio.wait(pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            break
        for task in done:
            search_query = tasks[task]
            try:
                query_candidates = task.result()
                candidates.extend(query_candidates)
                await _emit_research_event_async(
                    event_callback,
                    "web_query_done",
                    query=search_query,
                    result_count=len(query_candidates),
                    web_sources=_trace_sources(query_candidates),
                )
            except Exception as exc:
                error = f"{search_query}: {_friendly_search_error(exc)}"
                errors.append(error)
                await _emit_research_event_async(event_callback, "web_query_error", query=search_query, error=error)
    for task in pending:
        search_query = tasks[task]
        error = f"{search_query}: 搜索超时"
        errors.append(error)
        await _emit_research_event_async(event_callback, "web_query_error", query=search_query, error=error)
        task.cancel()
    if errors and not candidates:
        warnings.append(f"联网搜索失败：{'; '.join(errors[:2])}")
    elif errors:
        warnings.append(f"部分联网搜索失败，已使用可用结果：{'; '.join(errors[:2])}")
    return _dedupe_web_results(candidates)[:MAX_WEB_CANDIDATES]


def rank_web_sources(
    query: str,
    candidates: list[dict[str, str]],
    *,
    source_preferences: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Backward-compatible wrapper for deterministic web-source prefiltering.

    The output keeps the historical ``rank_*`` fields for callers and tests,
    but domain trust/preference signals are metadata only.  Evidence relevance
    and authority decisions belong to the Evidence Critic.
    """
    return prefilter_web_sources(
        candidates,
        source_preferences=source_preferences,
    )


def prefilter_web_sources(
    candidates: list[dict[str, str]],
    *,
    source_preferences: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Apply deterministic URL/content checks and attach retrieval metadata."""
    filtered: list[dict[str, Any]] = []
    preferences = _sanitize_source_preferences(source_preferences or {})
    registry = get_registry()
    seen_urls: set[str] = set()
    for position, item in enumerate(candidates):
        title = item.get("title", "")
        description = item.get("description", "")
        url = item.get("url", "")
        domain = _domain(url)
        normalized_url = _clean_web_url(url).lower()
        if not normalized_url or normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        if not title and not description:
            continue
        rank_score = max(MAX_WEB_CANDIDATES - position, 0) * 0.1
        quality_tags: list[str] = list(item.get("quality_tags") or [])
        is_preferred_domain = _domain_in_list(domain, preferences["preferred_domains"])
        generic_homepage = _is_generic_homepage_url(url) and not is_preferred_domain
        if generic_homepage:
            quality_tags.append("generic_homepage")
        domain_scores = registry.domain_scores(domain)
        domain_roles = registry.domain_roles(domain)
        trusted = bool(domain_scores["trusted"]) and not generic_homepage
        if trusted:
            quality_tags.append("trusted_domain")
        if is_preferred_domain and not generic_homepage:
            trusted = True
            quality_tags.append("planner_preferred_domain")
        if preferences["prefer_primary_sources"] and domain_scores["official"] and not generic_homepage:
            quality_tags.append("planner_primary_source")
        if domain_scores["low_quality"]:
            trusted = False
            quality_tags.append("low_quality_domain")
        if domain_scores["weak_secondary"]:
            trusted = False
            quality_tags.append("weak_secondary_source")
        quality_tags.extend(_source_identity_quality_tags(item, domain))
        if _is_language_dictionary_source(domain):
            quality_tags.append("language_dictionary_source")
        filtered.append(
            {
                **item,
                "domain": domain,
                "rank_score": rank_score,
                "trusted_source": trusted,
                "matched_terms": [],
                "domain_scores": domain_scores,
                "domain_roles": domain_roles,
                "domain_trust_score": domain_scores["trusted"],
                "prefilter_passed": True,
                "prefilter_reason": "passed",
                "quality_tags": _unique(quality_tags),
            }
        )
    return filtered


def evaluate_search_quality(
    query: str,
    ranked_sources: list[dict[str, Any]],
    *,
    source_preferences: dict[str, Any] | None = None,
    subject: dict[str, Any] | None = None,
    evidence_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not ranked_sources:
        return _empty_search_quality("no_results")
    preferences = _sanitize_source_preferences(source_preferences or {})
    contract = _sanitize_evidence_contract(evidence_contract or {})
    subject_terms = _subject_terms(query, subject)
    top_five = ranked_sources[:5]
    top_score = float(ranked_sources[0].get("rank_score") or 0)
    trusted_sources = sum(1 for item in top_five if item.get("trusted_source"))
    preferred_sources = sum(
        1
        for item in top_five
        if _source_satisfies_preferences(item, preferences)
    )
    matched_terms = sorted({term for item in top_five for term in item.get("matched_terms", [])})
    matched_subject_terms: list[str] = []
    core_terms = subject_terms
    top = ranked_sources[0]
    low_quality = (
        "low_quality_domain" in (top.get("quality_tags") or [])
        or "generic_homepage" in (top.get("quality_tags") or [])
    )
    weak_secondary = _is_weak_secondary_source(top)
    has_content = any(
        bool((item.get("title") or "").strip() or (item.get("description") or "").strip())
        for item in ranked_sources[:MAX_WEB_RESULTS]
    )
    passed = has_content
    reason = "passed" if passed else "top results had no readable search summaries"
    return {
        "passed": passed,
        "score": top_score,
        "reason": reason,
        "matched_terms": matched_terms,
        "matched_subject_terms": matched_subject_terms,
        "core_terms": core_terms,
        "required_terms": subject_terms,
        "trusted_sources": trusted_sources,
        "preferred_sources": preferred_sources,
        "top_result_hints": {
            "low_quality": low_quality,
            "weak_secondary": weak_secondary,
            "generic_homepage": _is_generic_homepage_source(top),
            "must_cover_subject": bool(contract.get("must_cover_subject")),
        },
        "evidence_status": "sufficient" if passed else "insufficient",
        "rewrite_attempted": False,
        "rewritten_queries": [],
    }


def _rank_and_evaluate_results(
    query: str,
    results: list[dict[str, str]],
    research_plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ranked = rank_web_sources(query, results, source_preferences=research_plan.get("source_preferences") or {})
    quality = evaluate_search_quality(
        query,
        ranked,
        source_preferences=research_plan.get("source_preferences") or {},
        subject=research_plan.get("subject") or {},
        evidence_contract=research_plan.get("evidence_contract") or {},
    )
    quality["retrieval_health"] = _retrieval_health(ranked, search_quality=quality, research_plan=research_plan)
    return ranked, quality


def _retrieval_health(
    ranked_sources: list[dict[str, Any]],
    *,
    search_quality: dict[str, Any],
    research_plan: dict[str, Any],
) -> dict[str, Any]:
    top_sources = ranked_sources[:MAX_WEB_RESULTS]
    candidate_count = len(top_sources)
    domains = {str(item.get("domain") or _domain(item.get("url") or "")) for item in top_sources if item.get("url")}
    generic_count = sum(1 for item in top_sources if _is_generic_homepage_source(item))
    weak_count = sum(1 for item in top_sources if _is_weak_secondary_source(item))
    low_quality_count = sum(1 for item in top_sources if _is_low_quality_source(item))
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    minimum_sources = int(contract.get("minimum_independent_sources") or 1)
    min_candidates = _min_recall_candidates_for_plan(research_plan)
    min_domains = _min_recall_domains_for_plan(research_plan)
    weak_or_generic_count = generic_count + weak_count + low_quality_count
    all_candidates_weak_or_generic = candidate_count > 0 and weak_or_generic_count >= candidate_count
    weak_or_generic_repair = (
        all_candidates_weak_or_generic
        and bool(contract.get("prefer_authoritative_sources"))
        and not _allows_interpretive_secondary_recall(research_plan)
    )
    broad_weak_or_generic_repair = (
        _plan_requires_full_search_budget(research_plan)
        and candidate_count > 0
        and weak_or_generic_count >= max(2, candidate_count // 2)
        and not _allows_interpretive_secondary_recall(research_plan)
    )
    needs_repair = (
        not bool(search_quality.get("passed"))
        or candidate_count < min_candidates
        or len(domains) < min_domains
        or weak_or_generic_repair
        or broad_weak_or_generic_repair
    )
    return {
        "candidate_count": candidate_count,
        "independent_domain_count": len(domains),
        "generic_homepage_count": generic_count,
        "weak_secondary_count": weak_count,
        "low_quality_count": low_quality_count,
        "minimum_candidate_count": min_candidates,
        "minimum_independent_domain_count": min_domains,
        "needs_recall_repair": needs_repair,
        "repair_reason": _retrieval_repair_reason(
            search_quality=search_quality,
            candidate_count=candidate_count,
            independent_domain_count=len(domains),
            generic_count=generic_count,
            weak_count=weak_count,
            low_quality_count=low_quality_count,
            min_candidates=min_candidates,
            min_domains=min_domains,
            minimum_sources=minimum_sources,
            weak_or_generic_repair=weak_or_generic_repair or broad_weak_or_generic_repair,
        ),
    }


def rewrite_queries_for_low_quality_results(
    query: str,
    *,
    existing_queries: list[str],
    failed_results: list[dict[str, Any]] | None = None,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    query_planner_adapter: ResearchQueryPlannerAdapter | None = None,
) -> list[str]:
    plan = _planned_research_plan(
        query,
        mode="web_only",
        article_context={
            **(article_context or {}),
            "search_repair": {
                "existing_queries": existing_queries[:MAX_WEB_QUERIES],
                "failed_results": [
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "description": _clip_text(str(item.get("description") or ""), 180),
                    }
                    for item in (failed_results or [])[:5]
                ],
            },
        },
        selection_context=selection_context,
        query_planner_adapter=query_planner_adapter,
    )
    rewrites = list((plan or {}).get("queries") or [])
    existing = set(item.lower() for item in existing_queries)
    return [item for item in _unique_queries(rewrites) if item.lower() not in existing][:MAX_WEB_QUERIES]


def _queries_with_preferred_domains(
    queries: list[str],
    source_preferences: dict[str, Any],
    *,
    max_queries: int | None = None,
) -> list[str]:
    preferences = _sanitize_source_preferences(source_preferences or {})
    preferred_domains = _preferred_query_domains(preferences)
    query_limit = _normalized_search_query_limit(max_queries)
    domain_slots = min(len(preferred_domains), max(query_limit - 1, 0), 2)
    base_limit = query_limit - domain_slots if preferred_domains else query_limit
    base_queries = _unique_queries(queries)[:base_limit]
    if not base_queries or not preferred_domains:
        return base_queries
    augmented = list(base_queries)
    for domain in preferred_domains:
        clean_domain = re.sub(r"[^A-Za-z0-9_.-]", "", str(domain or "").lower()).strip(".")
        if not clean_domain:
            continue
        query = base_queries[0]
        if f"site:{clean_domain}" in query.lower():
            continue
        augmented.append(f"{query} site:{clean_domain}")
        if len(augmented) >= query_limit:
            break
    return _unique_queries(augmented)[:query_limit]


def _preferred_domain_limited_queries(queries: list[str], source_preferences: dict[str, Any]) -> list[str]:
    base_queries = _unique_queries(queries)
    if not base_queries:
        return []
    output: list[str] = []
    for domain in _preferred_query_domains(_sanitize_source_preferences(source_preferences or {})):
        clean_domain = re.sub(r"[^A-Za-z0-9_.-]", "", str(domain or "").lower()).strip(".")
        if not clean_domain:
            continue
        output.append(f"{base_queries[0]} site:{clean_domain}")
        if len(output) >= 2:
            break
    return _unique_queries(output)


def _post_critic_repair_queries(
    query: str,
    research_plan: dict[str, Any],
    queries: list[str],
    rewritten_queries: list[str],
) -> list[str]:
    existing = {item.lower() for item in [*queries, *rewritten_queries]}
    subject = research_plan.get("subject") if isinstance(research_plan.get("subject"), dict) else {}
    names = [
        str(subject.get("name") or "").strip(),
        *[str(item or "").strip() for item in subject.get("aliases") or []],
    ]
    names = [item for item in _unique_queries(names) if item]
    seeds: list[str] = []
    for name in names[:2]:
        seeds.append(f"{name} {query}".strip())
        intent = str(research_plan.get("intent_label") or "").strip()
        if intent:
            seeds.append(f"{name} {intent}".strip())
        if _contains_cjk(name) or _contains_cjk(query):
            seeds.append(f"\"{name}\" \"{query}\"")
    output: list[str] = []
    for seed in seeds:
        value = re.sub(r"\s+", " ", seed).strip()
        if not value or value.lower() in existing:
            continue
        output.append(value[:120])
    return _unique_queries(output)[:2]


def _direct_retrieval_candidates(
    query: str,
    research_plan: dict[str, Any],
    *,
    existing_results: list[dict[str, str]],
) -> list[dict[str, str]]:
    subject = research_plan.get("subject") if isinstance(research_plan.get("subject"), dict) else {}
    candidates = _direct_preferred_domain_candidates(
        query,
        research_plan.get("source_preferences") or {},
        existing_results=existing_results,
        subject=subject,
    )
    candidates = _dedupe_web_results(candidates + existing_results)[:MAX_WEB_CANDIDATES]
    kind_candidates = _direct_source_kind_candidates(
        query,
        research_plan,
        existing_results=candidates,
    )
    return _dedupe_web_results(kind_candidates + candidates)[:MAX_WEB_CANDIDATES]


def _direct_preferred_domain_candidates(
    query: str,
    source_preferences: dict[str, Any],
    *,
    existing_results: list[dict[str, str]],
    subject: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    preferences = _sanitize_source_preferences(source_preferences or {})
    existing_urls = {_clean_web_url(item.get("url") or "") for item in existing_results if item.get("url")}
    candidates: list[dict[str, str]] = []
    for domain in preferences.get("preferred_domains") or []:
        clean_domain = re.sub(r"[^A-Za-z0-9_.-]", "", str(domain or "").lower()).strip(".")
        if not clean_domain:
            continue
        for url in _preferred_domain_probe_urls(query, clean_domain, subject=subject):
            if not _direct_probe_budget_available():
                break
            if _clean_web_url(url) in existing_urls:
                continue
            fetched = _fetch_url_summary(url)
            if fetched:
                candidates.append({**fetched, "query": f"direct:{clean_domain}"})
                break
    return candidates[:2]


def _direct_source_kind_candidates(
    query: str,
    research_plan: dict[str, Any],
    *,
    existing_results: list[dict[str, str]],
) -> list[dict[str, str]]:
    preferences = _sanitize_source_preferences(research_plan.get("source_preferences") or {})
    kinds = {str(kind).strip().lower() for kind in preferences.get("preferred_source_kinds") or []}
    if not ({"finance_glossary", "glossary", "dictionary", "术语表"} & kinds):
        return []
    if _results_include_source_kind(existing_results, preferences, min_independent_domains=2):
        return []
    existing_urls = {_clean_web_url(item.get("url") or "") for item in existing_results if item.get("url")}
    candidates: list[dict[str, str]] = []
    for url in _finance_glossary_probe_urls(query, research_plan.get("subject") if isinstance(research_plan.get("subject"), dict) else {}):
        if not _direct_probe_budget_available():
            break
        cleaned = _clean_web_url(url)
        if cleaned in existing_urls:
            continue
        fetched = _fetch_url_summary(url)
        if fetched:
            candidates.append({**fetched, "query": f"direct:{_domain(url)}"})
            existing_urls.add(cleaned)
        if len(candidates) >= 2:
            break
    return candidates


def _direct_mentioned_url_candidates(
    results: list[dict[str, Any]],
    research_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    preferences = _sanitize_source_preferences(research_plan.get("source_preferences") or {})
    subject = research_plan.get("subject") if isinstance(research_plan.get("subject"), dict) else {}
    existing_urls = {_clean_web_url(str(item.get("url") or "")) for item in results if isinstance(item, dict)}
    candidates: list[dict[str, Any]] = []
    for item in results[:MAX_WEB_CANDIDATES]:
        if not isinstance(item, dict):
            continue
        source_domain = _domain(str(item.get("url") or ""))
        text = " ".join([str(item.get("title") or ""), str(item.get("description") or "")])
        for url in _mentioned_urls(text):
            cleaned = _clean_web_url(url)
            if not cleaned or cleaned in existing_urls:
                continue
            domain = _domain(cleaned)
            if not domain or (source_domain and _same_or_parent_domain(source_domain, domain)):
                continue
            _reg = get_registry()
            if _reg.low_quality_score(domain) or _reg.weak_secondary_score(domain):
                continue
            if not _mentioned_url_matches_plan(cleaned, domain, subject, preferences):
                continue
            fetched = _fetch_url_summary(cleaned) if _direct_probe_budget_available() else None
            candidates.append(
                {
                    "title": (fetched or {}).get("title") or cleaned,
                    "url": cleaned,
                    "description": (fetched or {}).get("description") or str(item.get("description") or ""),
                    "query": "direct:mentioned-url",
                    "quality_tags": ["mentioned_url_candidate"],
                }
            )
            existing_urls.add(cleaned)
            if len(candidates) >= 3:
                return candidates
    return candidates
