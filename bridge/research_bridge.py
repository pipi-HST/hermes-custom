"""Shared research bridge for local knowledge + controlled web research."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from datetime import datetime
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.capture_agent import load_config
from bridge.result_envelope import success_envelope

ResearchSearchAdapter = Callable[[str, int], str | dict[str, Any]]
ResearchExtractAdapter = Callable[[list[str]], str | dict[str, Any] | Awaitable[str | dict[str, Any]]]
ResearchSynthesizeAdapter = Callable[[dict[str, Any]], str]
ResearchQueryPlannerAdapter = Callable[[dict[str, Any]], str | list[str] | dict[str, Any]]

VALID_MODES = {"current_article_only", "local_only", "local_web", "web_only"}
WEB_INTENT_RE = re.compile(
    r"联网|网上|搜索|搜一下|查一下|外部|公开信息|最新|最近|今天|现在|今年|新闻|"
    r"市场|行情|走势|A股|港股|美股|指数|价格|政策|法律|法规|版本|发布|更新|公司动态|还成立|变化|"
    r"芯片|半导体|专业|学科|定律|概念"
)
LOCAL_ONLY_RE = re.compile(r"我的笔记|我之前保存|知识库里|这篇文章什么意思|解释选区")
HIGH_RISK_RE = re.compile(r"市场|股票|炒股|投资|涨|跌|政策|法律|法规|医疗|诊断|药|价格|利率|汇率|财报")
MAX_WEB_RESULTS = 5
MAX_EXTRACT_URLS = 3
MAX_WEB_QUERIES = 4
MAX_WEB_CANDIDATES = 12
WEB_SEARCH_TIMEOUT_SECONDS = float(os.environ.get("HERMES_WEB_SEARCH_TIMEOUT_SECONDS", "35"))
DEFAULT_RESEARCH_YEAR = int(os.environ.get("HERMES_WEB_RESEARCH_YEAR", str(datetime.now().year)))

ENTITY_QUERY_EXPANSIONS = {
    "苹果": "Apple AAPL",
    "英伟达": "Nvidia NVDA",
    "特斯拉": "Tesla TSLA",
    "微软": "Microsoft MSFT",
    "亚马逊": "Amazon AMZN",
    "谷歌": "Alphabet Google GOOGL",
    "贵州茅台": "Kweichow Moutai 600519",
    "茅台": "Kweichow Moutai 600519",
    "腾讯": "Tencent 0700.HK",
}

QUERY_KEYWORD_EXPANSIONS = {
    "市值": ["market cap today", "market capitalization current"],
    "股价": ["stock price today", "share price current"],
    "财报": ["earnings financial results", "latest earnings"],
    "价格": ["price today", "current price"],
}

FINANCE_DOMAINS = {
    "companiesmarketcap.com": 9,
    "finance.yahoo.com": 8,
    "nasdaq.com": 8,
    "macrotrends.net": 7,
    "investing.com": 7,
    "marketwatch.com": 6,
    "reuters.com": 6,
    "bloomberg.com": 6,
    "cnbc.com": 5,
    "sina.com.cn": 4,
    "stcn.com": 4,
    "21jingji.com": 4,
    "xueqiu.com": 3,
}

OFFICIAL_DOMAINS = {
    "federalreserve.gov": 12,
    "sec.gov": 10,
    "whitehouse.gov": 9,
    "congress.gov": 9,
    "gov.cn": 9,
    "pbc.gov.cn": 10,
    "csrc.gov.cn": 10,
    "sse.com.cn": 9,
    "szse.cn": 9,
}

CHINA_MARKET_DOMAINS = {
    "eastmoney.com": 8,
    "finance.eastmoney.com": 9,
    "finance.sina.com.cn": 8,
    "stcn.com": 8,
    "cs.com.cn": 8,
    "cnstock.com": 8,
    "cls.cn": 8,
    "21jingji.com": 7,
    "sse.com.cn": 8,
    "szse.cn": 8,
    "p5w.net": 6,
    "xueqiu.com": 4,
}

NEWS_DOMAINS = {
    "reuters.com": 8,
    "bloomberg.com": 8,
    "cnbc.com": 7,
    "wsj.com": 7,
    "ft.com": 7,
    "sina.com.cn": 5,
    "stcn.com": 5,
    "21jingji.com": 5,
    "xueqiu.com": 3,
}

LOW_QUALITY_DOMAINS = {
    "douyin.com": -8,
    "iesdouyin.com": -8,
    "toutiao.com": -5,
    "ixigua.com": -5,
}

MARKET_TERMS_RE = re.compile(r"A股|港股|美股|上证指数|创业板指?|沪深300|中证500|A-share|A shares", re.I)
MARKET_RECAP_RE = re.compile(r"走势|行情|复盘|反弹|下跌|上涨|表现|回顾|市场")
SELECTION_DEPENDENT_RE = re.compile(r"这句话|这段|文中|上面|这个判断|这里说的|还成立|有没有更新|有没有新证据|这.*现在")
SELECTION_INDEPENDENT_RE = re.compile(r"是谁|是什么|多少|现在的|最新的|今天的|当前")
ENTITY_EXPLAINER_RE = re.compile(r"什么是|是什么|是什么东西|什么意思|介绍一下|what\s+is|who\s+is|define", re.I)
LAW_CONCEPT_RE = re.compile(r"([\w\u0370-\u03ff\u4e00-\u9fff]{1,24})\s*定律|([\w\u0370-\u03ff]{1,24})\s+law", re.I)
GREEK_LETTER_NAMES = {
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "δ": "delta",
    "ε": "epsilon",
    "ζ": "zeta",
    "η": "eta",
    "θ": "theta",
    "ι": "iota",
    "κ": "kappa",
    "λ": "lambda",
    "μ": "mu",
    "ν": "nu",
    "ξ": "xi",
    "ο": "omicron",
    "π": "pi",
    "ρ": "rho",
    "σ": "sigma",
    "τ": "tau",
    "υ": "upsilon",
    "φ": "phi",
    "χ": "chi",
    "ψ": "psi",
    "ω": "omega",
}


def research_answer(
    *,
    query: str,
    mode: str = "local_web",
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    export_file: bool = False,
    search_adapter: ResearchSearchAdapter | None = None,
    extract_adapter: ResearchExtractAdapter | None = None,
    synthesize_adapter: ResearchSynthesizeAdapter | None = None,
) -> dict[str, Any]:
    """Answer a research question with current article, local RAG, and/or web evidence."""
    normalized_mode = _normalize_mode(mode)
    clean_query = (query or "").strip()
    if not clean_query:
        return success_envelope(
            agent="research",
            intent="research_answer",
            chat_preview="请先输入要研究的问题。",
            meta={"query": query, "mode": normalized_mode},
        )

    article_context = article_context or {}
    selection_context = selection_context or {}
    use_article = bool(article_context)
    use_local = normalized_mode in {"local_only", "local_web"}
    use_web = normalized_mode in {"web_only", "local_web"} and should_search_web(clean_query, normalized_mode)

    warnings: list[str] = []
    local_answer = ""
    knowledge_sources: list[dict[str, Any]] = []
    if use_local:
        local_answer, knowledge_sources = _search_local(clean_query, warnings)

    web_sources: list[dict[str, Any]] = []
    web_research: dict[str, Any] = _empty_web_research(clean_query, article_context, selection_context)
    if use_web:
        web_research = _run_web_research(
            clean_query,
            warnings,
            article_context=article_context,
            selection_context=selection_context,
            search_adapter=search_adapter,
            extract_adapter=extract_adapter,
        )
        web_sources = web_research["sources"]

    synthesis_input = {
        "query": clean_query,
        "mode": normalized_mode,
        "article_context": article_context,
        "selection_context": selection_context,
        "local_answer": local_answer,
        "knowledge_sources": knowledge_sources,
        "web_sources": web_sources,
        "web_queries": web_research["queries"],
        "query_intent": web_research["query_intent"],
        "search_quality": web_research["search_quality"],
        "rewritten_web_queries": web_research["rewritten_queries"],
        "provider_status": web_research["provider_status"],
        "used_article": use_article,
        "used_local": use_local,
        "used_web": bool(web_sources),
    }
    if _should_use_low_quality_fallback(synthesis_input):
        answer = _low_quality_fallback_answer(synthesis_input)
    else:
        answer = (
            synthesize_adapter(synthesis_input)
            if synthesize_adapter
            else _synthesize_answer(synthesis_input, warnings)
        )
    answer = _ensure_valid_citations(answer, synthesis_input, warnings)
    if use_web and not web_sources:
        warnings.append("当前没有可用的联网搜索结果，已基于可用的本地知识库和当前文章回答。")
    if _is_high_risk_query(clean_query):
        warnings.append("该问题涉及高风险或强时效判断，请优先核对官方公告、原始报告或主流可靠来源；AI 综合不构成投资、法律或医疗建议。")

    cited_refs = _cited_refs(answer)
    cited_knowledge_sources = _filter_cited_sources(knowledge_sources, cited_refs)
    cited_web_sources = _filter_cited_sources(web_sources, cited_refs)
    source_map = [
        _source_map_item(source, "knowledge", index)
        for index, source in enumerate(cited_knowledge_sources, 1)
    ] + [
        _source_map_item(source, "web", index)
        for index, source in enumerate(cited_web_sources, 1)
    ]

    return success_envelope(
        agent="research",
        intent="research_answer",
        chat_preview=answer,
        source_map=source_map,
        warnings=_unique(warnings),
        meta={
            "query": clean_query,
            "mode": normalized_mode,
            "answer": answer,
            "knowledge_sources": cited_knowledge_sources,
            "web_sources": cited_web_sources,
            "retrieved_knowledge_sources": knowledge_sources,
            "retrieved_web_sources": web_research["retrieved_sources"],
            "web_queries": synthesis_input["web_queries"],
            "query_intent": synthesis_input["query_intent"],
            "search_quality": synthesis_input["search_quality"],
            "rewritten_web_queries": synthesis_input["rewritten_web_queries"],
            "provider_status": synthesis_input["provider_status"],
            "used_article": use_article,
            "used_local": use_local,
            "used_web": bool(web_sources),
            "web_search_attempted": use_web,
            "export_file": export_file,
        },
    )


async def async_research_answer(
    *,
    query: str,
    mode: str = "local_web",
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    export_file: bool = False,
    search_adapter: ResearchSearchAdapter | None = None,
    extract_adapter: ResearchExtractAdapter | None = None,
    synthesize_adapter: ResearchSynthesizeAdapter | None = None,
) -> dict[str, Any]:
    """Async variant for FastAPI routes so web extraction can be awaited safely."""
    normalized_mode = _normalize_mode(mode)
    clean_query = (query or "").strip()
    if not clean_query:
        return success_envelope(
            agent="research",
            intent="research_answer",
            chat_preview="请先输入要研究的问题。",
            meta={"query": query, "mode": normalized_mode},
        )

    article_context = article_context or {}
    selection_context = selection_context or {}
    use_article = bool(article_context)
    use_local = normalized_mode in {"local_only", "local_web"}
    use_web = normalized_mode in {"web_only", "local_web"} and should_search_web(clean_query, normalized_mode)

    warnings: list[str] = []
    local_answer = ""
    knowledge_sources: list[dict[str, Any]] = []
    if use_local:
        local_answer, knowledge_sources = await asyncio.to_thread(_search_local, clean_query, warnings)

    web_sources: list[dict[str, Any]] = []
    web_research: dict[str, Any] = _empty_web_research(clean_query, article_context, selection_context)
    if use_web:
        web_research = await _run_web_research_async(
            clean_query,
            warnings,
            article_context=article_context,
            selection_context=selection_context,
            search_adapter=search_adapter,
            extract_adapter=extract_adapter,
        )
        web_sources = web_research["sources"]

    synthesis_input = {
        "query": clean_query,
        "mode": normalized_mode,
        "article_context": article_context,
        "selection_context": selection_context,
        "local_answer": local_answer,
        "knowledge_sources": knowledge_sources,
        "web_sources": web_sources,
        "web_queries": web_research["queries"],
        "query_intent": web_research["query_intent"],
        "search_quality": web_research["search_quality"],
        "rewritten_web_queries": web_research["rewritten_queries"],
        "provider_status": web_research["provider_status"],
        "used_article": use_article,
        "used_local": use_local,
        "used_web": bool(web_sources),
    }
    if _should_use_low_quality_fallback(synthesis_input):
        answer = _low_quality_fallback_answer(synthesis_input)
    else:
        answer = (
            synthesize_adapter(synthesis_input)
            if synthesize_adapter
            else await asyncio.to_thread(_synthesize_answer, synthesis_input, warnings)
        )
    answer = _ensure_valid_citations(answer, synthesis_input, warnings)
    if use_web and not web_sources:
        warnings.append("当前没有可用的联网搜索结果，已基于可用的本地知识库和当前文章回答。")
    if _is_high_risk_query(clean_query):
        warnings.append("该问题涉及高风险或强时效判断，请优先核对官方公告、原始报告或主流可靠来源；AI 综合不构成投资、法律或医疗建议。")

    cited_refs = _cited_refs(answer)
    cited_knowledge_sources = _filter_cited_sources(knowledge_sources, cited_refs)
    cited_web_sources = _filter_cited_sources(web_sources, cited_refs)
    source_map = [
        _source_map_item(source, "knowledge", index)
        for index, source in enumerate(cited_knowledge_sources, 1)
    ] + [
        _source_map_item(source, "web", index)
        for index, source in enumerate(cited_web_sources, 1)
    ]

    return success_envelope(
        agent="research",
        intent="research_answer",
        chat_preview=answer,
        source_map=source_map,
        warnings=_unique(warnings),
        meta={
            "query": clean_query,
            "mode": normalized_mode,
            "answer": answer,
            "knowledge_sources": cited_knowledge_sources,
            "web_sources": cited_web_sources,
            "retrieved_knowledge_sources": knowledge_sources,
            "retrieved_web_sources": web_research["retrieved_sources"],
            "used_article": use_article,
            "used_local": use_local,
            "used_web": bool(web_sources),
            "web_queries": synthesis_input["web_queries"],
            "query_intent": synthesis_input["query_intent"],
            "search_quality": synthesis_input["search_quality"],
            "rewritten_web_queries": synthesis_input["rewritten_web_queries"],
            "provider_status": synthesis_input["provider_status"],
            "web_search_attempted": use_web,
            "export_file": export_file,
        },
    )


def should_search_web(query: str, mode: str = "local_web") -> bool:
    normalized_mode = _normalize_mode(mode)
    if normalized_mode == "web_only":
        return True
    if normalized_mode in {"local_only", "current_article_only"}:
        return False
    if LOCAL_ONLY_RE.search(query or "") and not WEB_INTENT_RE.search(query or ""):
        return False
    return bool(WEB_INTENT_RE.search(query or ""))


def _is_high_risk_query(query: str) -> bool:
    return bool(HIGH_RISK_RE.search(query or ""))


def _is_finance_fact_query(query: str) -> bool:
    return bool(re.search(r"市值|股价|股本|财报|估值|汇率|利率|market\s*cap|stock\s*price|earnings", query or "", re.I))


def _is_recent_news_query(query: str) -> bool:
    return bool(re.search(r"最近|最新|今天|现在|新闻|发布|公告|动态|recent|latest|today|news|announce", query or "", re.I))


def detect_query_intent(query: str, selection_context: dict[str, Any] | None = None) -> str:
    text = (query or "").strip()
    selection = str((selection_context or {}).get("selected_text") or "")
    if _question_depends_on_selection(text, selection) or SELECTION_DEPENDENT_RE.search(text):
        return "article_check"
    if re.search(r"美联储.*主席|Federal\s+Reserve.*Chair|Fed\s+Chair", text, re.I):
        return "official_fact"
    if re.search(r"现任|当前|current", text, re.I) and re.search(r"主席|总统|CEO|首席执行官|负责人|chair|president", text, re.I):
        return "official_fact"
    if MARKET_TERMS_RE.search(text) and MARKET_RECAP_RE.search(text):
        return "market_recap"
    if _is_finance_fact_query(text):
        return "finance_fact"
    if _is_recent_news_query(text):
        return "recent_news"
    if ENTITY_EXPLAINER_RE.search(text):
        return "entity_explainer"
    return "general_web"


def _question_depends_on_selection(query: str, selection: str) -> bool:
    if not (selection or "").strip():
        return False
    text = query or ""
    if SELECTION_DEPENDENT_RE.search(text):
        return True
    if SELECTION_INDEPENDENT_RE.search(text):
        return False
    return False


def _normalize_mode(mode: str) -> str:
    value = (mode or "local_web").strip()
    return value if value in VALID_MODES else "local_web"


def _search_local(query: str, warnings: list[str]) -> tuple[str, list[dict[str, Any]]]:
    try:
        from bridge.rag_bridge import search_knowledge_base

        result = search_knowledge_base(query)
    except Exception as exc:
        warnings.append(f"本地知识库检索失败：{exc}")
        return "", []

    meta = result.get("meta") if isinstance(result, dict) else {}
    source_map = result.get("source_map") if isinstance(result, dict) else []
    answer = str(meta.get("answer") or result.get("chat_preview") or "").strip()
    sources: list[dict[str, Any]] = []
    if isinstance(source_map, list):
        for index, item in enumerate(source_map[:8], 1):
            if not isinstance(item, dict):
                continue
            sources.append(
                {
                    "ref": f"K{index}",
                    "type": "knowledge",
                    "id": str(item.get("id") or ""),
                    "title": str(item.get("title") or item.get("path") or f"知识来源 {index}"),
                    "path": str(item.get("path") or ""),
                    "section": str(item.get("section") or ""),
                    "excerpt": str(item.get("excerpt") or item.get("section") or ""),
                }
            )
    return answer, sources


def _search_web(
    query: str,
    warnings: list[str],
    *,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    search_adapter: ResearchSearchAdapter | None,
    extract_adapter: ResearchExtractAdapter | None,
) -> list[dict[str, Any]]:
    return _run_web_research(
        query,
        warnings,
        article_context=article_context,
        selection_context=selection_context,
        search_adapter=search_adapter,
        extract_adapter=extract_adapter,
    )["sources"]


def _empty_web_research(
    query: str,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    intent = detect_query_intent(query, selection_context=selection_context)
    queries = plan_web_queries(query, article_context=article_context, selection_context=selection_context)
    return {
        "sources": [],
        "retrieved_sources": [],
        "queries": queries,
        "query_intent": intent,
        "search_quality": _empty_search_quality("not_searched"),
        "rewritten_queries": [],
        "provider_status": {"search_elapsed_ms": 0, "warnings": []},
    }


def _run_web_research(
    query: str,
    warnings: list[str],
    *,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    search_adapter: ResearchSearchAdapter | None,
    extract_adapter: ResearchExtractAdapter | None,
) -> dict[str, Any]:
    search_adapter = search_adapter or _default_web_search
    extract_adapter = extract_adapter or _default_web_extract
    intent = detect_query_intent(query, selection_context=selection_context)
    queries = plan_web_queries(query, article_context=article_context, selection_context=selection_context)
    warning_start = len(warnings)
    started = time.monotonic()
    results = collect_web_search_results(queries, search_adapter=search_adapter, warnings=warnings)
    ranked = rank_web_sources(query, results, query_intent=intent)
    quality = evaluate_search_quality(query, intent, ranked)
    rewritten_queries: list[str] = []
    if results and not quality["passed"]:
        rewritten_queries = rewrite_queries_for_low_quality_results(
            query,
            intent,
            existing_queries=queries,
            selection_context=selection_context,
        )
        if rewritten_queries:
            rewritten_results = collect_web_search_results(
                rewritten_queries,
                search_adapter=search_adapter,
                warnings=warnings,
            )
            results = _dedupe_web_results(rewritten_results + results)
            ranked = rank_web_sources(query, results, query_intent=intent)
            quality = evaluate_search_quality(query, intent, ranked)
            quality["rewrite_attempted"] = True
            quality["rewritten_queries"] = rewritten_queries
    provider_status = {
        "search_elapsed_ms": int((time.monotonic() - started) * 1000),
        "warnings": warnings[warning_start:],
    }
    retrieved_sources = _web_sources_from_ranked(ranked, {})
    if results and not quality["passed"]:
        warnings.append("联网搜索结果相关性不足，已尝试重写搜索词；仍未找到足够可靠来源。")
        provider_status["warnings"] = warnings[warning_start:]
        return {
            "sources": [],
            "retrieved_sources": retrieved_sources,
            "queries": queries,
            "query_intent": intent,
            "search_quality": quality,
            "rewritten_queries": rewritten_queries,
            "provider_status": provider_status,
        }
    sources = extract_ranked_web_sources(ranked, extract_adapter=extract_adapter, warnings=warnings)
    return {
        "sources": sources,
        "retrieved_sources": sources,
        "queries": queries,
        "query_intent": intent,
        "search_quality": quality,
        "rewritten_queries": rewritten_queries,
        "provider_status": provider_status,
    }


async def _search_web_async(
    query: str,
    warnings: list[str],
    *,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    search_adapter: ResearchSearchAdapter | None,
    extract_adapter: ResearchExtractAdapter | None,
) -> list[dict[str, Any]]:
    return (await _run_web_research_async(
        query,
        warnings,
        article_context=article_context,
        selection_context=selection_context,
        search_adapter=search_adapter,
        extract_adapter=extract_adapter,
    ))["sources"]


async def _run_web_research_async(
    query: str,
    warnings: list[str],
    *,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    search_adapter: ResearchSearchAdapter | None,
    extract_adapter: ResearchExtractAdapter | None,
) -> dict[str, Any]:
    search_adapter = search_adapter or _default_web_search
    extract_adapter = extract_adapter or _default_web_extract
    intent = detect_query_intent(query, selection_context=selection_context)
    queries = plan_web_queries(query, article_context=article_context, selection_context=selection_context)
    warning_start = len(warnings)
    started = time.monotonic()
    results = await collect_web_search_results_async(queries, search_adapter=search_adapter, warnings=warnings)
    ranked = rank_web_sources(query, results, query_intent=intent)
    quality = evaluate_search_quality(query, intent, ranked)
    rewritten_queries: list[str] = []
    if results and not quality["passed"]:
        rewritten_queries = rewrite_queries_for_low_quality_results(
            query,
            intent,
            existing_queries=queries,
            selection_context=selection_context,
        )
        if rewritten_queries:
            rewritten_results = await collect_web_search_results_async(
                rewritten_queries,
                search_adapter=search_adapter,
                warnings=warnings,
            )
            results = _dedupe_web_results(rewritten_results + results)
            ranked = rank_web_sources(query, results, query_intent=intent)
            quality = evaluate_search_quality(query, intent, ranked)
            quality["rewrite_attempted"] = True
            quality["rewritten_queries"] = rewritten_queries
    provider_status = {
        "search_elapsed_ms": int((time.monotonic() - started) * 1000),
        "warnings": warnings[warning_start:],
    }
    retrieved_sources = _web_sources_from_ranked(ranked, {})
    if results and not quality["passed"]:
        warnings.append("联网搜索结果相关性不足，已尝试重写搜索词；仍未找到足够可靠来源。")
        provider_status["warnings"] = warnings[warning_start:]
        return {
            "sources": [],
            "retrieved_sources": retrieved_sources,
            "queries": queries,
            "query_intent": intent,
            "search_quality": quality,
            "rewritten_queries": rewritten_queries,
            "provider_status": provider_status,
        }
    sources = await extract_ranked_web_sources_async(ranked, extract_adapter=extract_adapter, warnings=warnings)
    return {
        "sources": sources,
        "retrieved_sources": sources,
        "queries": queries,
        "query_intent": intent,
        "search_quality": quality,
        "rewritten_queries": rewritten_queries,
        "provider_status": provider_status,
    }


def plan_web_queries(
    query: str,
    *,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    query_planner_adapter: ResearchQueryPlannerAdapter | None = None,
) -> list[str]:
    """Generate compact web-search queries without leaking private article text."""
    raw_query = (query or "").strip()
    selection = str((selection_context or {}).get("selected_text") or "")
    intent = detect_query_intent(raw_query, selection_context=selection_context)
    use_selection = _question_depends_on_selection(raw_query, selection)
    entity_hint = _entity_hint(raw_query, selection if use_selection else "")
    keyword_expansions = _keyword_expansions(raw_query)

    queries: list[str] = []
    llm_queries = _planned_web_queries(
        raw_query,
        intent=intent,
        article_context=article_context,
        selection_context=selection_context,
        query_planner_adapter=query_planner_adapter,
    )
    if llm_queries:
        return _unique_queries(llm_queries)[:MAX_WEB_QUERIES]

    concept_queries = _concept_definition_queries(raw_query, article_context=article_context, selection_context=selection_context)
    if concept_queries:
        queries.extend(concept_queries)
    elif intent == "official_fact":
        if re.search(r"美联储|Federal\s+Reserve|Fed", raw_query, re.I):
            queries.extend(
                [
                    "site:federalreserve.gov Federal Reserve Chair current",
                    "Federal Reserve Board Chair current",
                    "site:federalreserve.gov Federal Reserve chair sworn in 2026",
                    "现任 美联储 主席",
                ]
            )
        else:
            compact = _search_query_from_private_context(raw_query)
            queries.extend([f"{compact} 官方 current", compact])
    elif intent == "market_recap":
        month = _query_month(raw_query)
        year = _query_year(raw_query) or DEFAULT_RESEARCH_YEAR
        market = _market_hint(raw_query) or "A股"
        if month:
            queries.extend(
                [
                    f"{year}年{month}月 {market} 行情 回顾 上证指数 创业板指",
                    f"{year}年{month}月 {market} 走势 复盘 沪深300",
                    f"{month}月 {market} 市场表现 上证指数 创业板指",
                ]
            )
        else:
            queries.extend(
                [
                    f"{year}年 {market} 行情 回顾 上证指数 创业板指",
                    f"{market} 走势 复盘 沪深300",
                ]
            )
    elif intent == "article_check" and use_selection:
        compact_selection = _search_query_from_private_context(selection)
        compact_query = _search_query_from_private_context(raw_query)
        if compact_selection:
            queries.extend([f"{compact_selection} 最新 公开信息", f"{compact_selection} {compact_query}"])
    elif entity_hint and keyword_expansions:
        queries.extend(f"{entity_hint} {item}" for item in keyword_expansions[:2])
    elif entity_hint and _is_recent_news_query(raw_query):
        queries.extend([f"{entity_hint} latest news", f"{entity_hint} recent public comments"])
    elif entity_hint:
        queries.append(f"{entity_hint} {raw_query}")

    if re.search(r"[A-Za-z]{1,6}", raw_query) and keyword_expansions:
        queries.append(f"{raw_query} {keyword_expansions[0]}")

    if intent != "market_recap":
        compact = _search_query_from_private_context(raw_query)
        if compact:
            queries.append(compact)
    if use_selection and selection.strip() and selection.strip() not in raw_query:
        queries.append(_search_query_from_private_context(f"{selection} {raw_query}"))

    return _unique_queries(queries)[:MAX_WEB_QUERIES] or [raw_query[:200]]


def collect_web_search_results(
    queries: list[str],
    *,
    search_adapter: ResearchSearchAdapter,
    warnings: list[str],
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    errors: list[str] = []
    search_queries = _unique_queries(queries)[:MAX_WEB_QUERIES]
    if not search_queries:
        return []
    executor = ThreadPoolExecutor(max_workers=min(len(search_queries), MAX_WEB_QUERIES))
    futures = {
        executor.submit(_search_query_candidates, search_query, search_adapter): search_query
        for search_query in search_queries
    }
    try:
        for future in as_completed(futures, timeout=WEB_SEARCH_TIMEOUT_SECONDS):
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
) -> list[dict[str, str]]:
    raw = search_adapter(search_query, MAX_WEB_RESULTS)
    return [{**item, "query": search_query} for item in _parse_web_search(raw)]


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
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    errors: list[str] = []
    search_queries = _unique_queries(queries)[:MAX_WEB_QUERIES]
    if not search_queries:
        return []
    tasks = {
        asyncio.create_task(asyncio.to_thread(_search_query_candidates, search_query, search_adapter)): search_query
        for search_query in search_queries
    }
    done, pending = await asyncio.wait(tasks.keys(), timeout=WEB_SEARCH_TIMEOUT_SECONDS)
    for task in done:
        search_query = tasks[task]
        try:
            candidates.extend(task.result())
        except Exception as exc:
            errors.append(f"{search_query}: {_friendly_search_error(exc)}")
    for task in pending:
        search_query = tasks[task]
        errors.append(f"{search_query}: 搜索超时")
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
    query_intent: str | None = None,
) -> list[dict[str, Any]]:
    ranked = []
    query_type = query_intent or detect_query_intent(query)
    terms = set(_extract_core_terms(query))
    for item in candidates:
        title = item.get("title", "")
        description = item.get("description", "")
        url = item.get("url", "")
        domain = _domain(url)
        haystack = f"{title} {description} {url}".lower()
        haystack_text = f"{title} {description} {url}"
        score = 0.0
        quality_tags: list[str] = []
        trusted = False
        domain_points = 0
        if query_type == "official_fact":
            domain_points = _domain_score(domain, OFFICIAL_DOMAINS)
            score += domain_points
            if domain_points:
                trusted = True
                quality_tags.append("trusted_domain")
            if re.search(r"federal\s+reserve|board\s+of\s+governors|chair|主席|现任", haystack, re.I):
                score += 4
        elif query_type == "market_recap":
            domain_points = _domain_score(domain, CHINA_MARKET_DOMAINS)
            score += domain_points
            if domain_points:
                trusted = True
                quality_tags.append("trusted_domain")
            if MARKET_TERMS_RE.search(haystack_text):
                score += 3
                quality_tags.append("core_entity_match")
            if MARKET_RECAP_RE.search(haystack_text):
                score += 2
        elif query_type == "finance_fact":
            score += _domain_score(domain, FINANCE_DOMAINS)
            if _domain_score(domain, FINANCE_DOMAINS):
                trusted = True
                quality_tags.append("trusted_domain")
            if re.search(r"market\s*cap|market capitalization|市值|aapl|stock price|股价", haystack, re.I):
                score += 4
            if re.search(r"historical|history|all-time|曾|历史|最高", haystack, re.I):
                score -= 2
        elif query_type == "recent_news":
            score += _domain_score(domain, NEWS_DOMAINS)
            if _domain_score(domain, NEWS_DOMAINS):
                trusted = True
                quality_tags.append("trusted_domain")
            if re.search(r"latest|recent|today|news|最新|最近|今天|公告|发布", haystack, re.I):
                score += 3
        elif query_type == "entity_explainer":
            entity = _entity_name_for_explainer(query)
            if entity and _domain_matches_entity(domain, entity):
                score += 6
                trusted = True
                quality_tags.append("official_source")
            if _looks_like_entity_intro_page(url, title, description):
                score += 2
                quality_tags.append("intro_page")
            if entity and entity.lower() in haystack:
                score += 2
                quality_tags.append("core_entity_match")
            if _is_financing_or_valuation_side_topic(haystack_text):
                score -= 4
                quality_tags.append("timely_side_topic")
        else:
            domain_points = min(_domain_score(domain, {**OFFICIAL_DOMAINS, **FINANCE_DOMAINS, **NEWS_DOMAINS}), 5)
            score += domain_points
            if domain_points:
                trusted = True
                quality_tags.append("trusted_domain")
        matched_terms = [term for term in terms if term and term.lower() in haystack]
        score += len(matched_terms) * 1.2
        if matched_terms:
            quality_tags.append("core_term_match")
        score += _domain_score(domain, LOW_QUALITY_DOMAINS)
        if _domain_score(domain, LOW_QUALITY_DOMAINS):
            quality_tags.append("low_quality_domain")
        if re.search(r"抖音|短视频|明天走势怎么看", haystack_text):
            score -= 4
            quality_tags.append("low_quality_format")
        if not title:
            score -= 1
        if not description:
            score -= 0.5
        ranked.append(
            {
                **item,
                "domain": domain,
                "rank_score": score,
                "query_type": query_type,
                "trusted_source": trusted,
                "matched_terms": matched_terms,
                "quality_tags": _unique(quality_tags),
            }
        )
    return sorted(ranked, key=lambda item: item.get("rank_score", 0), reverse=True)


def evaluate_search_quality(
    query: str,
    intent: str,
    ranked_sources: list[dict[str, Any]],
) -> dict[str, Any]:
    if not ranked_sources:
        return _empty_search_quality("no_results")
    top_five = ranked_sources[:5]
    top_score = float(ranked_sources[0].get("rank_score") or 0)
    trusted_sources = sum(1 for item in top_five if item.get("trusted_source"))
    matched_terms = sorted({term for item in top_five for term in item.get("matched_terms", [])})
    core_terms = _extract_core_terms(query)
    core_match_count = len(matched_terms)
    if intent not in {"official_fact", "market_recap", "finance_fact"}:
        top = ranked_sources[0]
        low_quality = "low_quality_domain" in (top.get("quality_tags") or [])
        has_content = bool((top.get("title") or "").strip() or (top.get("description") or "").strip())
        passed = has_content and not low_quality
        return {
            "passed": passed,
            "score": top_score,
            "reason": "passed" if passed else "top result was low quality or empty",
            "matched_terms": matched_terms,
            "core_terms": core_terms,
            "trusted_sources": trusted_sources,
            "rewrite_attempted": False,
            "rewritten_queries": [],
        }
    passed = (
        top_score >= _quality_score_threshold(intent)
        or (trusted_sources >= 1 and core_match_count >= 1)
        or core_match_count >= 2
    )
    if intent in {"official_fact", "market_recap", "finance_fact"}:
        passed = passed and (trusted_sources >= 1 or top_score >= _quality_score_threshold(intent))
    reason = "passed" if passed else "top results did not meet trusted source or core term requirements"
    return {
        "passed": passed,
        "score": top_score,
        "reason": reason,
        "matched_terms": matched_terms,
        "core_terms": core_terms,
        "trusted_sources": trusted_sources,
        "rewrite_attempted": False,
        "rewritten_queries": [],
    }


def rewrite_queries_for_low_quality_results(
    query: str,
    intent: str,
    *,
    existing_queries: list[str],
    selection_context: dict[str, Any] | None = None,
) -> list[str]:
    rewrites: list[str] = []
    if intent == "official_fact":
        if re.search(r"美联储|Federal\s+Reserve|Fed", query, re.I):
            rewrites.extend(
                [
                    "site:federalreserve.gov about the Fed Chair Board of Governors",
                    "site:federalreserve.gov Board of Governors Chair current",
                    "site:federalreserve.gov Federal Reserve chair sworn in 2026",
                ]
            )
    elif intent == "market_recap":
        month = _query_month(query)
        year = _query_year(query) or DEFAULT_RESEARCH_YEAR
        market = _market_hint(query) or "A股"
        if month:
            rewrites.extend(
                [
                    f"{year}年{month}月 {market} 上证指数 创业板指 沪深300 复盘",
                    f"{year}年{month}月 {market} 市场 回顾 证券时报 东方财富",
                ]
            )
        else:
            rewrites.append(f"{year}年 {market} 上证指数 创业板指 沪深300 复盘")
    elif intent == "finance_fact":
        entity_hint = _entity_hint(query)
        keyword_expansions = _keyword_expansions(query)
        if entity_hint and keyword_expansions:
            rewrites.extend(f"{entity_hint} {item}" for item in keyword_expansions)
    else:
        compact = _search_query_from_private_context(query)
        if compact:
            rewrites.append(compact)
    existing = set(item.lower() for item in existing_queries)
    return [item for item in _unique_queries(rewrites) if item.lower() not in existing][:MAX_WEB_QUERIES]


def _empty_search_quality(reason: str) -> dict[str, Any]:
    return {
        "passed": False,
        "score": 0.0,
        "reason": reason,
        "matched_terms": [],
        "core_terms": [],
        "trusted_sources": 0,
        "rewrite_attempted": False,
        "rewritten_queries": [],
    }


def _quality_score_threshold(intent: str) -> float:
    if intent == "official_fact":
        return 10.0
    if intent == "market_recap":
        return 8.0
    if intent == "finance_fact":
        return 8.0
    return 5.0


def extract_ranked_web_sources(
    ranked: list[dict[str, Any]],
    *,
    extract_adapter: ResearchExtractAdapter,
    warnings: list[str],
) -> list[dict[str, Any]]:
    urls = [item["url"] for item in ranked[:MAX_EXTRACT_URLS] if item.get("url")]
    extracted_by_url: dict[str, str] = {}
    if urls:
        try:
            extracted_by_url = _parse_web_extract(extract_adapter(urls))
        except Exception as exc:
            warnings.append(f"网页正文抽取失败，已仅使用搜索摘要：{exc}")
    return _web_sources_from_ranked(ranked, extracted_by_url)


async def extract_ranked_web_sources_async(
    ranked: list[dict[str, Any]],
    *,
    extract_adapter: ResearchExtractAdapter,
    warnings: list[str],
) -> list[dict[str, Any]]:
    urls = [item["url"] for item in ranked[:MAX_EXTRACT_URLS] if item.get("url")]
    extracted_by_url: dict[str, str] = {}
    if urls:
        try:
            raw = extract_adapter(urls)
            if asyncio.iscoroutine(raw):
                raw = await raw
            extracted_by_url = _parse_web_extract(raw)
        except Exception as exc:
            warnings.append(f"网页正文抽取失败，已仅使用搜索摘要：{exc}")
    return _web_sources_from_ranked(ranked, extracted_by_url)


def _web_sources_from_ranked(ranked: list[dict[str, Any]], extracted_by_url: dict[str, str]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for index, item in enumerate(ranked[:MAX_WEB_RESULTS], 1):
        url = item.get("url", "")
        excerpt = (extracted_by_url.get(url) or item.get("description") or "").strip()
        sources.append(
            {
                "ref": f"W{index}",
                "type": "web",
                "id": url or f"web-{index}",
                "title": item.get("title") or url or f"网页来源 {index}",
                "url": url,
                "domain": item.get("domain") or _domain(url),
                "query": item.get("query") or "",
                "excerpt": excerpt[:1800],
                "description": item.get("description") or "",
                "extract_status": "full" if extracted_by_url.get(url) else "summary",
                "rank_score": item.get("rank_score", 0),
                "query_intent": item.get("query_type") or "",
                "quality_tags": item.get("quality_tags") or [],
            }
        )
    return sources


def _query_year(query: str) -> int | None:
    match = re.search(r"(20\d{2})\s*年?", query or "")
    return int(match.group(1)) if match else None


def _query_month(query: str) -> int | None:
    text = query or ""
    digit = re.search(r"(?<!\d)(1[0-2]|[1-9])\s*月份?", text)
    if digit:
        return int(digit.group(1))
    chinese_months = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "十一": 11,
        "十二": 12,
    }
    for label, value in sorted(chinese_months.items(), key=lambda item: len(item[0]), reverse=True):
        if f"{label}月" in text:
            return value
    return None


def _market_hint(query: str) -> str:
    match = MARKET_TERMS_RE.search(query or "")
    if not match:
        return ""
    value = match.group(0)
    if value.lower() in {"a-share", "a shares"}:
        return "A股"
    if value == "创业板":
        return "创业板指"
    return value


def _planned_web_queries(
    query: str,
    *,
    intent: str,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    query_planner_adapter: ResearchQueryPlannerAdapter | None = None,
) -> list[str]:
    adapter = query_planner_adapter or _default_query_planner
    try:
        raw = adapter(_query_planner_payload(query, intent, article_context, selection_context))
    except Exception:
        return []
    return _parse_query_plan(raw)


def _query_planner_payload(
    query: str,
    intent: str,
    article_context: dict[str, Any] | None,
    selection_context: dict[str, Any] | None,
) -> dict[str, Any]:
    article_context = article_context or {}
    selection_context = selection_context or {}
    return {
        "query": query,
        "intent": intent,
        "current_date": _current_date_for_prompt(),
        "selected_text": _clip_text(str(selection_context.get("selected_text") or ""), 120),
        "surrounding_before": _clip_text(str(selection_context.get("surrounding_before") or ""), 260),
        "surrounding_after": _clip_text(str(selection_context.get("surrounding_after") or ""), 260),
        "heading_path": selection_context.get("heading_path") or [],
        "article_title": _clip_text(str(article_context.get("title") or ""), 120),
        "article_summary": _clip_text(str(article_context.get("summary") or ""), 260),
    }


def _default_query_planner(payload: dict[str, Any]) -> str | list[str] | dict[str, Any]:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    if not config.llm_api_key:
        return []
    prompt = _build_query_planner_prompt(payload)
    return _call_llm(
        api_key=config.llm_api_key,
        model=config.llm_model,
        base_url=config.llm_base_url or "https://api.deepseek.com",
        prompt=prompt,
    )


def _build_query_planner_prompt(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "你是 Hermes 的网页搜索 query planner。只输出 JSON，不要解释。",
            "",
            "任务：根据用户问题、选区和少量上下文生成 1-4 条网页搜索 query。",
            "",
            "规则：",
            "- 保留用户原始关键短语。",
            "- 如果问题包含符号、缩写、外文、同音/转写歧义，你可以生成可能的别名、英文转写或行业说法。",
            "- 如果选区上下文能消歧，使用少量上下文关键词；不要复制长段私有文本。",
            "- 如果是官方事实/当前职位/实时信息，优先生成官方来源或英文官方 query。",
            "- 如果是实体解释类问题，由你判断实体类型并规划合适 query；优先考虑官方/权威介绍，但不要机械套固定后缀。",
            "- 如果是专业、概念、定义、行业入门问题，生成通用检索 query，不依赖固定词表。",
            "- 每条 query 不超过 80 个字或 120 个英文字符。",
            "",
            '输出格式：{"queries":["...","..."]}',
            "",
            f"当前日期：{payload.get('current_date')}",
            f"问题类型：{payload.get('intent')}",
            f"用户问题：{payload.get('query')}",
            f"选中文本：{payload.get('selected_text')}",
            f"前文：{payload.get('surrounding_before')}",
            f"后文：{payload.get('surrounding_after')}",
            f"标题路径：{' > '.join(payload.get('heading_path') or [])}",
            f"文章标题：{payload.get('article_title')}",
            f"文章摘要：{payload.get('article_summary')}",
        ]
    )


def _parse_query_plan(raw: str | list[str] | dict[str, Any]) -> list[str]:
    if isinstance(raw, list):
        return _sanitize_planned_queries(raw)
    if isinstance(raw, dict):
        candidates = raw.get("queries") or raw.get("query") or raw.get("results") or []
        if isinstance(candidates, str):
            candidates = [candidates]
        return _sanitize_planned_queries(candidates if isinstance(candidates, list) else [])
    text = (raw or "").strip()
    if not text:
        return []
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        lines = [line.strip("-• \t") for line in text.splitlines()]
        return _sanitize_planned_queries(lines)
    return _parse_query_plan(payload)


def _sanitize_planned_queries(items: list[Any]) -> list[str]:
    queries = []
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        value = re.sub(r"\s+", " ", value)
        if len(value) > 220:
            value = _search_query_from_private_context(value)
        queries.append(value[:220])
    return _unique_queries(queries)[:MAX_WEB_QUERIES]


def _concept_definition_queries(
    query: str,
    *,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
) -> list[str]:
    text = (query or "").strip()
    if not re.search(r"什么是|是什么|什么意思|解释|概念|what\s+is|define", text, re.I):
        return []
    law_match = LAW_CONCEPT_RE.search(text)
    if not law_match:
        return []
    concept = _clean_concept_term(law_match.group(1) or law_match.group(2) or "")
    if not concept:
        return []
    context_terms = _context_terms_for_query(text, article_context=article_context, selection_context=selection_context)
    normalized = _normalize_symbolic_term(concept)
    queries = [
        f"{concept}定律 是什么",
        f"{concept} 定律 概念",
    ]
    if normalized and normalized.lower() != concept.lower():
        queries.extend([f"{normalized} law meaning", f"{normalized} law"])
    if context_terms:
        context = " ".join(context_terms[:4])
        queries.insert(0, f"{concept}定律 {context}")
        if normalized and normalized.lower() != concept.lower():
            queries.append(f"{normalized} law {context}")
    return _unique_queries(queries)


def _clean_concept_term(term: str) -> str:
    value = (term or "").strip()
    value = re.sub(r"^(什么是|解释一下|解释|请问|一个|这个|所谓)", "", value).strip()
    return value


def _normalize_symbolic_term(term: str) -> str:
    normalized = []
    for char in term:
        mapped = GREEK_LETTER_NAMES.get(char.lower())
        normalized.append(mapped if mapped else char)
    return "".join(normalized).strip()


def _context_terms_for_query(
    query: str,
    *,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
) -> list[str]:
    selection_context = selection_context or {}
    article_context = article_context or {}
    text = " ".join(
        str(value or "")
        for value in [
            selection_context.get("surrounding_before"),
            selection_context.get("selected_text"),
            selection_context.get("surrounding_after"),
            " ".join(selection_context.get("heading_path") or []),
            article_context.get("title"),
            _clip_text(str(article_context.get("summary") or ""), 240),
        ]
    )
    if not text.strip():
        return []
    terms = _extract_core_terms(text)
    query_terms = set(_extract_core_terms(query))
    return [term for term in terms if term not in query_terms][:8]


def _extract_core_terms(query: str) -> list[str]:
    text = query or ""
    protected: list[str] = []
    for pattern in [
        r"A股|港股|美股|上证指数|创业板指?|沪深300|中证500",
        r"AAPL|NVDA|TSLA|MSFT|GOOGL|GOOG|AMZN",
        r"Apple|Nvidia|Tesla|Microsoft|Alphabet|Amazon|Federal\s+Reserve|Fed",
        r"美联储|主席|市值|股价|财报|走势|行情|复盘|回顾",
    ]:
        protected.extend(match.group(0) for match in re.finditer(pattern, text, re.I))
    chunks = re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]{1,}[\u4e00-\u9fff]?|[\u4e00-\u9fff]{2,}", text)
    stopwords = {
        "是什么",
        "是谁",
        "多少",
        "现在",
        "当前",
        "最新",
        "今天",
        "这个",
        "这句话",
        "走势和行情是什么",
    }
    terms = []
    for term in protected + chunks:
        value = term.strip()
        if not value or value in stopwords:
            continue
        if any(stopword in value and value != stopword for stopword in {"是什么", "是谁", "多少"}):
            continue
        terms.append(value)
    return _unique_queries(terms)[:12]


def _entity_hint(*texts: str) -> str:
    joined = " ".join(text for text in texts if text)
    for key, value in ENTITY_QUERY_EXPANSIONS.items():
        if key in joined:
            return value
    ticker = re.search(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b", joined)
    return ticker.group(0) if ticker else ""


def _keyword_expansions(query: str) -> list[str]:
    expansions: list[str] = []
    for key, values in QUERY_KEYWORD_EXPANSIONS.items():
        if key in query:
            expansions.extend(values)
    if re.search(r"现在|当前|最新|今天", query):
        expansions.append("latest current today")
    return _unique_queries(expansions)


def _dedupe_web_results(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for item in items:
        key = item.get("url") or item.get("title") or item.get("description")
        key = (key or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _domain(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _domain_score(domain: str, scores: dict[str, int]) -> int:
    for candidate, score in scores.items():
        if domain == candidate or domain.endswith(f".{candidate}"):
            return score
    return 0


def _entity_name_for_explainer(query: str) -> str:
    text = (query or "").strip(" ?？。！!")
    patterns = [
        r"^(?:什么是|介绍一下)\s*(.+)$",
        r"^(.+?)(?:是什么东西|是什么|什么意思)$",
        r"^(?:what\s+is|who\s+is|define)\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip(" ?？。！!")
    terms = _extract_core_terms(text)
    return terms[0] if terms else ""


def _domain_matches_entity(domain: str, entity: str) -> bool:
    normalized_entity = re.sub(r"[^a-z0-9]+", "", entity.lower())
    if len(normalized_entity) < 3:
        return False
    normalized_domain = re.sub(r"[^a-z0-9]+", "", domain.lower().split(":")[0])
    return normalized_entity in normalized_domain


def _looks_like_entity_intro_page(url: str, title: str, description: str) -> bool:
    haystack = f"{url} {title} {description}"
    return bool(re.search(r"/about\b|/company\b|/product\b|/products\b|/docs\b|介绍|简介|是什么", haystack, re.I))


def _is_financing_or_valuation_side_topic(text: str) -> bool:
    return bool(re.search(r"融资|估值|投资|资本|股价|valuation|funding|investors?|market\s+cap", text or "", re.I))


def _unique_queries(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = re.sub(r"\s+", " ", (item or "").strip())
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            out.append(normalized[:220])
    return out


def _legacy_search_web(
    query: str,
    warnings: list[str],
    *,
    search_adapter: ResearchSearchAdapter | None,
    extract_adapter: ResearchExtractAdapter | None,
) -> list[dict[str, Any]]:
    search_adapter = search_adapter or _default_web_search
    extract_adapter = extract_adapter or _default_web_extract
    try:
        raw = search_adapter(_search_query_from_private_context(query), MAX_WEB_RESULTS)
        results = _parse_web_search(raw)
    except Exception as exc:
        warnings.append(f"联网搜索失败：{exc}")
        return []

    urls = [item["url"] for item in results[:MAX_EXTRACT_URLS] if item.get("url")]
    extracted_by_url: dict[str, str] = {}
    if urls:
        try:
            extracted_by_url = _parse_web_extract(extract_adapter(urls))
        except Exception as exc:
            warnings.append(f"网页正文抽取失败，已仅使用搜索摘要：{exc}")

    sources: list[dict[str, Any]] = []
    for index, item in enumerate(results[:MAX_WEB_RESULTS], 1):
        url = item.get("url", "")
        sources.append(
            {
                "ref": f"W{index}",
                "type": "web",
                "id": url or f"web-{index}",
                "title": item.get("title") or url or f"网页来源 {index}",
                "url": url,
                "excerpt": (extracted_by_url.get(url) or item.get("description") or "").strip()[:1800],
                "description": item.get("description") or "",
            }
        )
    return sources


def _default_web_search(query: str, limit: int) -> str:
    _ensure_main_repo_on_path()
    from tools.web_tools import web_search_tool

    return web_search_tool(query, limit=limit)


def _default_web_extract(urls: list[str]) -> str | dict[str, Any]:
    _ensure_main_repo_on_path()
    from tools.web_tools import web_extract_tool

    result = web_extract_tool(urls, format="markdown", use_llm_processing=True)
    if asyncio.iscoroutine(result):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(result)
        result.close()
        return {"results": []}
    return result


def _ensure_main_repo_on_path() -> None:
    main_root = os.environ.get("HERMES_AGENT_MAIN_ROOT", "/Users/pipi/Downloads/hermes-agent-main")
    if main_root and main_root not in sys.path:
        sys.path.insert(0, main_root)


def _parse_web_search(raw: str | dict[str, Any]) -> list[dict[str, str]]:
    payload = _json_payload(raw)
    if payload.get("success") is False:
        raise RuntimeError(str(payload.get("error") or "web_search failed"))
    candidates = payload.get("results")
    if candidates is None:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        candidates = data.get("web") or data.get("results") or []
    if not isinstance(candidates, list):
        return []
    results: list[dict[str, str]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or "").strip()
        title = str(item.get("title") or item.get("name") or url).strip()
        description = str(
            item.get("description")
            or item.get("snippet")
            or item.get("summary")
            or item.get("content")
            or ""
        ).strip()
        if url or title or description:
            results.append({"url": url, "title": title, "description": description})
    return results


def _parse_web_extract(raw: str | dict[str, Any]) -> dict[str, str]:
    payload = _json_payload(raw)
    if payload.get("success") is False:
        raise RuntimeError(str(payload.get("error") or "web_extract failed"))
    candidates = payload.get("results")
    if candidates is None:
        data = payload.get("data")
        if isinstance(data, dict):
            candidates = data.get("web") or data.get("results") or data.get("pages")
        elif isinstance(data, list):
            candidates = data
    if not isinstance(candidates, list):
        return {}
    extracted: dict[str, str] = {}
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("source_url") or "").strip()
        content = str(
            item.get("content")
            or item.get("markdown")
            or item.get("text")
            or item.get("summary")
            or item.get("description")
            or ""
        ).strip()
        if url and content:
            extracted[url] = content
    return extracted


def _json_payload(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"tool returned non-JSON response: {str(exc)}") from exc
    return value if isinstance(value, dict) else {"results": value}


def _synthesize_answer(payload: dict[str, Any], warnings: list[str]) -> str:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    if not config.llm_api_key:
        warnings.append("未配置 LLM API Key，已返回基于检索材料的结构化摘要。")
        return _fallback_answer(payload)
    prompt = _build_synthesis_prompt(payload)
    try:
        return _call_llm(
            api_key=config.llm_api_key,
            model=config.llm_model,
            base_url=config.llm_base_url or "https://api.deepseek.com",
            prompt=prompt,
        )
    except Exception as exc:
        warnings.append(f"综合回答生成失败，已返回检索材料摘要：{exc}")
        return _fallback_answer(payload)


def _build_synthesis_prompt(payload: dict[str, Any]) -> str:
    article = payload.get("article_context") or {}
    selection = payload.get("selection_context") or {}
    parts = [
        f"用户问题：{payload['query']}",
        f"研究模式：{payload['mode']}",
        f"当前日期：{_current_date_for_prompt()}",
        f"问题类型：{payload.get('query_intent') or 'general_web'}",
        "",
        "回答要求：",
        "- 用中文回答，先给结论，再分层说明依据。",
        "- 需要引用本地知识库时，只能使用提供的 [K1] [K2] ... 编号。",
        "- 需要引用联网资料时，只能使用提供的 [W1] [W2] ... 编号。",
        "- 不要编造来源；没有依据时明确说不确定。",
        "- 市场、政策、法律、医疗、价格等高风险或强时效问题，要优先依据官方公告、原始报告或主流可靠来源，并明确不确定性。",
        "- 回答“现任/当前/现在”类问题时，必须用当前日期校验任期、就任、卸任、宣誓等时间；如果某人任期已在当前日期前结束，不能再称其为现任。",
        "- 如果官方来源之间存在任期结束、临时代理、正式宣誓等交接信息，优先以时间最新且已生效的官方来源为准。",
        "- 对“是什么/什么是/什么意思”类实体解释问题，先直接回答用户问题，再说明它和选区的关系。",
        "- 选区只用于消歧和补充；如果用户问的是独立实体，不要把选区内容当成主问题本身。",
        "- 不要主动展开融资、估值、股价、投资等强时效旁支，除非用户明确询问这些内容。",
        "- 不要在回答正文末尾生成“参考来源”或来源清单；来源由界面统一展示在最底部。",
    ]
    if article:
        parts.extend(
            [
                "",
                "当前文章：",
                f"标题：{article.get('title') or '未命名'}",
                f"文件夹：{article.get('folder_path') or 'Inbox'}",
                f"正文摘录：\n{_clip_text(str(article.get('body') or ''), 8000)}",
            ]
        )
    if selection.get("selected_text"):
        parts.extend(
            [
                "",
                "用户选区：",
                f"标题路径：{' > '.join(selection.get('heading_path') or [])}",
                f"选中文本：\n{_clip_text(str(selection.get('selected_text') or ''), 3000)}",
                f"前文：\n{_clip_text(str(selection.get('surrounding_before') or ''), 1500)}",
                f"后文：\n{_clip_text(str(selection.get('surrounding_after') or ''), 1500)}",
            ]
        )
    if payload.get("local_answer"):
        parts.extend(["", "本地知识库回答草稿：", _clip_text(payload["local_answer"], 6000)])
    if payload.get("knowledge_sources"):
        parts.append("\n本地知识库来源：")
        for source in payload["knowledge_sources"]:
            parts.append(f"[{source['ref']}] {source.get('title', '')} {source.get('section', '')}".strip())
    if payload.get("web_sources"):
        parts.append("\n联网资料：")
        for source in payload["web_sources"]:
            parts.append(
                f"[{source['ref']}] {source.get('title', '')}\n"
                f"URL: {source.get('url', '')}\n"
                f"摘录：{_clip_text(source.get('excerpt', '') or source.get('description', ''), 1800)}"
            )
    return "\n".join(parts)


def _call_llm(*, api_key: str, model: str, base_url: str, prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是 Hermes Knowledge 的联网研究助手。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 3600,
    }
    with httpx.Client(timeout=180) as client:
        resp = client.post(f"{base_url}/chat/completions", headers=headers, json=body)
        resp.raise_for_status()
        payload = resp.json()
    return str(payload["choices"][0]["message"]["content"]).strip()


def _current_date_for_prompt() -> str:
    return os.environ.get("HERMES_CURRENT_DATE", datetime.now().strftime("%Y-%m-%d"))


def _fallback_answer(payload: dict[str, Any]) -> str:
    lines = [f"结论：已根据可用材料整理这个问题，但没有进行完整 LLM 综合。"]
    selection = payload.get("selection_context") or {}
    if selection.get("selected_text"):
        lines.extend(["", "选区：", _clip_text(str(selection.get("selected_text") or ""), 500)])
    if payload.get("local_answer"):
        lines.extend(["", "本地知识库依据：", _clip_text(payload["local_answer"], 1200)])
    if payload.get("web_sources"):
        lines.append("\n联网资料依据：")
        for source in payload["web_sources"][:5]:
            lines.append(f"- [{source['ref']}] {source.get('title', '')}: {_clip_text(source.get('excerpt') or source.get('description') or '', 260)}")
    article = payload.get("article_context") or {}
    if article.get("body") and not payload.get("local_answer") and not payload.get("web_sources"):
        lines.extend(["", "当前文章依据：", _clip_text(str(article.get("body") or ""), 600)])
    return "\n".join(lines).strip()


def _should_use_low_quality_fallback(payload: dict[str, Any]) -> bool:
    quality = payload.get("search_quality") or {}
    if quality.get("reason") == "not_searched":
        return False
    if payload.get("web_sources"):
        return False
    if payload.get("mode") == "web_only":
        return _requires_source_backed_fallback(payload)
    return bool(payload.get("rewritten_web_queries") and not quality.get("passed"))


def _requires_source_backed_fallback(payload: dict[str, Any]) -> bool:
    query = str(payload.get("query") or "")
    intent = str(payload.get("query_intent") or "")
    if _is_high_risk_query(query):
        return True
    return intent in {"official_fact", "market_recap", "finance_fact", "recent_news", "article_check"}


def _low_quality_fallback_answer(payload: dict[str, Any]) -> str:
    quality = payload.get("search_quality") or {}
    lines = ["本次联网搜索没有找到足够可靠的公开资料来回答这个问题。"]
    queries = payload.get("web_queries") or []
    rewritten = payload.get("rewritten_web_queries") or []
    if queries:
        lines.extend(["", "已尝试的搜索词："])
        lines.extend(f"- {query}" for query in queries[:MAX_WEB_QUERIES])
    if rewritten:
        lines.extend(["", "已重写并再次搜索："])
        lines.extend(f"- {query}" for query in rewritten[:MAX_WEB_QUERIES])
    lines.extend(
        [
            "",
            "搜索结果主要问题：",
            f"- {quality.get('reason') or '来源相关性或权威性不足'}",
            f"- 权威来源命中数：{quality.get('trusted_sources', 0)}",
        ]
    )
    suggestions = _query_suggestions(payload.get("query") or "", payload.get("query_intent") or "")
    if suggestions:
        lines.extend(["", "建议改问："])
        lines.extend(f"- {item}" for item in suggestions)
    return "\n".join(lines).strip()


def _query_suggestions(query: str, intent: str) -> list[str]:
    if intent == "market_recap":
        month = _query_month(query) or 4
        year = _query_year(query) or DEFAULT_RESEARCH_YEAR
        market = _market_hint(query) or "A股"
        return [
            f"{year}年{month}月{market}行情回顾",
            f"{month}月{market}上证指数创业板走势",
        ]
    if intent == "official_fact":
        return ["官方资料中现任美联储主席是谁", "Federal Reserve current Chair official"]
    if intent == "finance_fact":
        return ["Apple AAPL market cap today", "CompaniesMarketCap Apple market cap"]
    return []


def _cited_refs(answer: str) -> set[str]:
    return {match.upper() for match in re.findall(r"\[([KW]\d+)\]", answer or "", flags=re.IGNORECASE)}


def _available_refs(payload: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for source in payload.get("knowledge_sources") or []:
        if isinstance(source, dict) and source.get("ref"):
            refs.add(str(source["ref"]).upper())
    for source in payload.get("web_sources") or []:
        if isinstance(source, dict) and source.get("ref"):
            refs.add(str(source["ref"]).upper())
    return refs


def _ensure_valid_citations(answer: str, payload: dict[str, Any], warnings: list[str]) -> str:
    cited_refs = _cited_refs(answer)
    if not cited_refs:
        return answer
    invalid_refs = sorted(cited_refs - _available_refs(payload))
    if not invalid_refs:
        return answer
    warnings.append(
        f"模型返回了未提供的来源编号（{', '.join(invalid_refs[:5])}），已改用可验证检索摘要。"
    )
    return _fallback_answer(payload)


def _filter_cited_sources(sources: list[dict[str, Any]], cited_refs: set[str]) -> list[dict[str, Any]]:
    if not cited_refs:
        return []
    return [
        source
        for source in sources
        if str(source.get("ref") or "").upper() in cited_refs
    ]


def _source_map_item(source: dict[str, Any], source_type: str, index: int) -> dict[str, Any]:
    ref = str(source.get("ref") or f"{'K' if source_type == 'knowledge' else 'W'}{index}")
    return {
        "role": "evidence",
        "type": source_type,
        "id": str(source.get("id") or source.get("url") or ref),
        "title": str(source.get("title") or ref),
        "path": str(source.get("path") or source.get("url") or ""),
        "url": str(source.get("url") or ""),
        "section": str(source.get("section") or ""),
        "status": "",
        "ref": ref,
        "excerpt": str(source.get("excerpt") or ""),
    }


def _search_query_from_private_context(query: str) -> str:
    normalized = re.sub(r"\s+", " ", query or "").strip()
    terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]{1,}[\u4e00-\u9fff]?|[\u4e00-\u9fff]{2,}", normalized)
    if not terms:
        return normalized[:200]
    compact = " ".join(terms[:18])
    compact = re.sub(r"([A-Za-z0-9._-]+)是\s+(什么|谁|多少|哪)", r"\1是\2", compact)
    return compact


def _clip_text(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: int(limit * 0.7)].rstrip() + "\n...\n" + text[-int(limit * 0.25):].lstrip()


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out
