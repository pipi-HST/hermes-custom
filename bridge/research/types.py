"""Type aliases and constants for research bridge modules."""
from __future__ import annotations

import os
import re
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Awaitable, Callable

# Type aliases
ResearchSearchAdapter = Callable[[str, int], str | dict[str, Any]]
ResearchExtractAdapter = Callable[[list[str]], str | dict[str, Any] | Awaitable[str | dict[str, Any]]]
ResearchSynthesizeAdapter = Callable[[dict[str, Any]], str]
ResearchQueryPlannerAdapter = Callable[[dict[str, Any]], str | list[str] | dict[str, Any]]
ResearchEventCallback = Callable[[dict[str, Any]], None | Awaitable[None]]
ResearchEvidenceCriticAdapter = Callable[[dict[str, Any]], str | dict[str, Any]]

# Valid modes
VALID_MODES = {"current_article_only", "local_only", "local_web", "web_only"}

# Search and extraction limits
MAX_WEB_RESULTS = 5
MAX_EXTRACT_URLS = 3
MAX_DYNAMIC_EXTRACT_URLS = 6
MAX_WEB_QUERIES = 4
MAX_WEB_CANDIDATES = 12

# Timeout configuration (seconds)
WEB_SEARCH_TIMEOUT_SECONDS = float(os.environ.get("HERMES_WEB_SEARCH_TIMEOUT_SECONDS", "35"))
PLANNER_LLM_TIMEOUT_SECONDS = float(os.environ.get("HERMES_PLANNER_LLM_TIMEOUT_SECONDS", "45"))
EVIDENCE_CRITIC_TIMEOUT_SECONDS = float(os.environ.get("HERMES_EVIDENCE_CRITIC_TIMEOUT_SECONDS", "45"))
MINIMAL_EVIDENCE_CRITIC_TIMEOUT_SECONDS = float(os.environ.get("HERMES_MINIMAL_EVIDENCE_CRITIC_TIMEOUT_SECONDS", "25"))
SYNTHESIS_LLM_TIMEOUT_SECONDS = float(os.environ.get("HERMES_SYNTHESIS_LLM_TIMEOUT_SECONDS", "120"))
RESEARCH_ANSWER_TIMEOUT_SECONDS = float(os.environ.get("HERMES_RESEARCH_ANSWER_TIMEOUT_SECONDS", "130"))
WEB_FALLBACK_MIN_REMAINING_SECONDS = float(os.environ.get("HERMES_WEB_FALLBACK_MIN_REMAINING_SECONDS", "40"))
EVIDENCE_RETRY_MIN_REMAINING_SECONDS = float(os.environ.get("HERMES_EVIDENCE_RETRY_MIN_REMAINING_SECONDS", "35"))
PLANNER_RESERVE_SECONDS = float(os.environ.get("HERMES_PLANNER_RESERVE_SECONDS", "80"))
WEB_SEARCH_RESERVE_SECONDS = float(os.environ.get("HERMES_WEB_SEARCH_RESERVE_SECONDS", "50"))
WEB_SEARCH_MIN_POST_RESERVE_SECONDS = float(os.environ.get("HERMES_WEB_SEARCH_MIN_POST_RESERVE_SECONDS", "12"))
EVIDENCE_CRITIC_RESERVE_SECONDS = float(os.environ.get("HERMES_EVIDENCE_CRITIC_RESERVE_SECONDS", "5"))
DIRECT_PROBE_TIMEOUT_SECONDS = float(os.environ.get("HERMES_DIRECT_PROBE_TIMEOUT_SECONDS", "3"))
DIRECT_PROBE_MIN_REMAINING_SECONDS = float(os.environ.get("HERMES_DIRECT_PROBE_MIN_REMAINING_SECONDS", "35"))
LOCAL_EXTRACT_TIMEOUT_SECONDS = float(os.environ.get("HERMES_LOCAL_EXTRACT_TIMEOUT_SECONDS", "8"))
LOCAL_EXTRACT_MAX_WORKERS = int(os.environ.get("HERMES_LOCAL_EXTRACT_MAX_WORKERS", "4"))

# Search backend configuration
SEARCH_ONLY_EXTRACT_BACKENDS = {"brave-free", "dashscope", "ddgs", "searxng"}
FALLBACK_SEARCH_BACKENDS = ("brave-free", "ddgs", "searxng", "tavily", "exa", "parallel", "firecrawl")
MAX_FALLBACK_SEARCH_BACKENDS = 2

# Query intent detection patterns
WEB_INTENT_RE = re.compile(
    r"联网|网上|搜索|搜一下|查一下|外部|公开信息|最新|最近|今天|现在|今年|新闻|"
    r"市场|行情|走势|A股|港股|美股|指数|价格|政策|法律|法规|版本|发布|更新|公司动态|还成立|变化|"
    r"芯片|半导体|专业|学科|定律|概念"
)
LOCAL_ONLY_RE = re.compile(r"我的笔记|我之前保存|知识库里|这篇文章什么意思|解释选区")
HIGH_RISK_RE = re.compile(r"市场|股票|炒股|投资|涨|跌|政策|法律|法规|医疗|诊断|药|价格|利率|汇率|财报")
MARKET_TERMS_RE = re.compile(r"A股|港股|美股|上证指数|创业板指?|沪深300|中证500|A-share|A shares", re.I)
MARKET_RECAP_RE = re.compile(r"走势|行情|复盘|反弹|下跌|上涨|表现|回顾|市场")
SELECTION_DEPENDENT_RE = re.compile(r"这句话|这段|文中|上面|这个判断|这里说的|还成立|有没有更新|有没有新证据|这.*现在")
SELECTION_INDEPENDENT_RE = re.compile(r"是谁|是什么|多少|现在的|最新的|今天的|当前")
ENTITY_EXPLAINER_RE = re.compile(r"什么是|是什么|是什么东西|什么意思|介绍一下|what\s+is|who\s+is|define", re.I)
LAW_CONCEPT_RE = re.compile(r"([\w\u0370-\u03ff\u4e00-\u9fff]{1,24})\s*定律|([\w\u0370-\u03ff]{1,24})\s+law", re.I)

# Year configuration
DEFAULT_RESEARCH_YEAR = int(os.environ.get("HERMES_WEB_RESEARCH_YEAR", str(datetime.now().year)))

# Research budget context variable
_RESEARCH_DEADLINE: ContextVar[float | None] = ContextVar("research_deadline", default=None)
