"""Research planning: query planning, plan parsing, and plan sanitization."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agents.source_registry import get_registry
from bridge.research.types import (
    ENTITY_EXPLAINER_RE,
    HIGH_RISK_RE,
    MAX_WEB_QUERIES,
    MARKET_RECAP_RE,
    MARKET_TERMS_RE,
    PLANNER_LLM_TIMEOUT_SECONDS,
    PLANNER_RESERVE_SECONDS,
    ResearchQueryPlannerAdapter,
    SELECTION_DEPENDENT_RE,
    SELECTION_INDEPENDENT_RE,
)
from bridge.research.search import (
    _normalize_mode,
)
from bridge.research.utils import (
    _clip_text,
    _coerce_bool,
    _current_date_for_prompt,
    _enum_value,
    _extract_core_terms,
    _required_query_terms,
    _sanitize_answer_contract,
    _sanitize_claim_requirements,
    _sanitize_evidence_contract,
    _sanitize_source_preferences,
    _search_query_from_private_context,
    _string_list,
    _unique,
    _unique_queries,
)


def plan_web_queries(
    query: str,
    *,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    query_planner_adapter: ResearchQueryPlannerAdapter | None = None,
) -> list[str]:
    """Generate web-search queries from planner output, with mechanical fallback only."""
    plan = plan_research(
        query,
        mode="web_only",
        article_context=article_context,
        selection_context=selection_context,
        query_planner_adapter=query_planner_adapter,
    )
    return list(plan.get("queries") or [])


def plan_research(
    query: str,
    *,
    mode: str = "local_web",
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    query_planner_adapter: ResearchQueryPlannerAdapter | None = None,
) -> dict[str, Any]:
    """Plan research: determine queries, source preferences, and evidence requirements."""
    raw_query = (query or "").strip()
    normalized_mode = _normalize_mode(mode)
    plan = _planned_research_plan(
        raw_query,
        mode=normalized_mode,
        article_context=article_context,
        selection_context=selection_context,
        query_planner_adapter=query_planner_adapter,
    )
    if plan:
        return _apply_mode_contract_to_plan(plan, raw_query, normalized_mode, selection_context=selection_context)
    return _fallback_research_plan(raw_query, normalized_mode, selection_context=selection_context)


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


def _apply_mode_contract_to_plan(
    plan: dict[str, Any],
    query: str,
    mode: str,
    *,
    selection_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    adjusted = dict(plan)
    adjusted["mode"] = mode
    if mode not in {"local_web", "web_only"}:
        return adjusted
    if adjusted.get("needs_web") and adjusted.get("queries"):
        return adjusted
    adjusted["needs_web"] = True
    subject = _fallback_subject_from_selection(query, selection_context)
    adjusted["queries"] = list(plan.get("queries") or []) or _fallback_queries(query, subject=subject)
    if subject and not (adjusted.get("subject") or {}).get("name"):
        adjusted["subject"] = _sanitize_subject({"name": subject}, query=query)
        adjusted["selection_role"] = "primary_subject"
    adjusted["plan_status"] = f"{plan.get('plan_status') or 'planned'}_web_forced_by_mode"
    return adjusted


def _normalized_search_query_limit(max_queries: int | None = None) -> int:
    if max_queries is None:
        return MAX_WEB_QUERIES
    try:
        parsed = int(max_queries)
    except (TypeError, ValueError):
        return MAX_WEB_QUERIES
    return max(1, min(parsed, MAX_WEB_QUERIES))


def _limited_search_queries(queries: list[str], max_queries: int | None = None) -> list[str]:
    return _unique_queries(queries)[:_normalized_search_query_limit(max_queries)]


def _plan_requires_full_search_budget(research_plan: dict[str, Any]) -> bool:
    contract = _sanitize_evidence_contract(research_plan.get("evidence_contract") or {})
    if research_plan.get("risk_level") == "high" or research_plan.get("freshness_sensitive"):
        return True
    if research_plan.get("answer_depth") == "detailed":
        return True
    if int(contract.get("minimum_independent_sources") or 1) > 1:
        return True
    if contract.get("must_use_primary_source"):
        return True
    claim_requirements = _sanitize_claim_requirements(research_plan.get("claim_requirements"))
    return any(
        requirement.get("required_authority") in {"primary_or_index", "primary_only"}
        for requirement in claim_requirements.values()
    )


def _max_initial_search_queries_for_plan(research_plan: dict[str, Any]) -> int:
    if _plan_requires_full_search_budget(research_plan):
        return MAX_WEB_QUERIES
    return min(2, MAX_WEB_QUERIES)


def _max_repair_search_queries_for_plan(research_plan: dict[str, Any]) -> int:
    if _plan_requires_full_search_budget(research_plan):
        return MAX_WEB_QUERIES
    return min(2, MAX_WEB_QUERIES)


def _planned_research_plan(
    query: str,
    *,
    mode: str,
    article_context: dict[str, Any] | None = None,
    selection_context: dict[str, Any] | None = None,
    query_planner_adapter: ResearchQueryPlannerAdapter | None = None,
) -> dict[str, Any] | None:
    import sys
    # Look up _default_query_planner from module globals to allow patching
    planner_module = sys.modules[__name__]
    adapter = query_planner_adapter or getattr(planner_module, "_default_query_planner", _default_query_planner)
    try:
        raw = adapter(_query_planner_payload(query, mode, article_context, selection_context))
    except Exception:
        return None
    return _parse_research_plan(raw, query=query, mode=mode, selection_context=selection_context)


def _query_planner_payload(
    query: str,
    mode: str,
    article_context: dict[str, Any] | None,
    selection_context: dict[str, Any] | None,
) -> dict[str, Any]:
    article_context = article_context or {}
    selection_context = selection_context or {}
    return {
        "query": query,
        "mode": mode,
        "current_date": _current_date_for_prompt(),
        "selected_text": _clip_text(str(selection_context.get("selected_text") or ""), 120),
        "surrounding_before": _clip_text(str(selection_context.get("surrounding_before") or ""), 260),
        "surrounding_after": _clip_text(str(selection_context.get("surrounding_after") or ""), 260),
        "heading_path": selection_context.get("heading_path") or [],
        "article_title": _clip_text(str(article_context.get("title") or ""), 120),
        "article_summary": _clip_text(str(article_context.get("summary") or ""), 260),
    }


def _default_query_planner(payload: dict[str, Any]) -> str | list[str] | dict[str, Any]:
    from config import load_config
    from bridge.research.synthesis import _call_llm

    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    if not config.llm_api_key:
        return []
    prompt = _build_query_planner_prompt(payload)
    return _call_llm(
        api_key=config.llm_api_key,
        model=config.llm_model,
        base_url=config.llm_base_url or "https://api.deepseek.com",
        prompt=prompt,
        json_mode=True,
        timeout_seconds=PLANNER_LLM_TIMEOUT_SECONDS,
        reserve_seconds=PLANNER_RESERVE_SECONDS,
    )


def _build_query_planner_prompt(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "你是 Hermes 的研究 planner。只输出 JSON，不要解释。",
            "",
            "任务：根据用户问题、选区和少量上下文，决定研究范围、网页检索 query、来源偏好和回答合同。",
            "",
            "规则：",
            "- 语义判断由你完成；代码只会校验 schema、裁剪长度和执行检索。",
            "- 如果模式是 web_only 或 local_web，needs_web 必须为 true，并必须给出可执行网页检索 query。",
            "- 如果模式是 local_only 或 current_article_only，needs_web 必须为 false，不要生成网页 query。",
            "- 需要网页资料时，生成 1-4 条 query；不需要时返回空数组。",
            "- query 可以使用必要的少量上下文词，但不要复制长段私有文章或选区。",
            "- 如果需要特定来源类型或要避开的旁支，请写入 source_preferences 或 answer_contract。",
            "- 必须写出 subject.name；如用户使用简称、中文名、英文名或 ticker，请把常见别名放入 subject.aliases。",
            "- 必须写出 evidence_contract：最低独立来源数、是否必须覆盖 subject、是否必须 primary source 都由你判断。",
            "- 如果答案需要评价、排名、指标、身份、最新事实等多个判断，请写出 answer_claims_needed；后续 critic 会逐项检查候选来源能否支撑这些 claim。",
            '- 对宽泛评价、介绍或"怎么样"问题，必须包含稳定的 overview/scope/reputation 等基础 claim；除非用户明确询问影响因子、排名、当前数值或最新指标，不要把这些指标设置成唯一或必须 claim。',
            "- 对影响因子、排名、当前数值、现任职位等强事实问题，也要保留一个稳定背景 claim（如 subject_overview/identity/scope），这样没有可靠数值证据时仍可说明已确认的对象身份，并明确数值未确认。",
            '- 对"为什么/怎么看/机制解释/剧情或政策逻辑分析"这类解释题，不要把尚未被来源证明的细节预先拆成很多具体 claims；优先使用 1-2 个宽泛解释 claim，如 answer_user_question 或 interpretive_explanation。',
            '- 写出 answer_depth：brief/normal/detailed。定义或简单事实用 brief/normal；"为什么/怎么样/如何评价/争议/机制解释"通常用 detailed，除非证据不足。',
            "- 如果某个 claim 需要官方、原始或索引数据库证据，请写入 claim_requirements；例如影响因子、JCR、SJR、排名、当前数值通常 required_authority=primary_or_index。",
            "- 涉及影响因子、JCR、排名、当前数值、最新事实或精确日期时，除非用户只问粗略常识，否则 evidence_contract.allow_summary_only 必须为 false。",
            "- 对低风险解释、观点梳理、剧情/政策机制分析类问题，如果不涉及当前数值、排名、法律/医疗/金融建议或精确日期，evidence_contract.allow_summary_only 通常应为 true，由 critic 判断摘要是否足以支撑解释。",
            '- 涉及"现任/当前/最新负责人/主席/CEO/总统/监管机构"等官方事实时，必须优先生成英文官方查询，并在 preferred_domains 写入你能确定的官方网站域名。',
            "- 对上述现任/当前/最新官方事实，claim_requirements 必须设置 required_authority=primary_or_index，evidence_contract.must_use_primary_source 必须为 true，allow_summary_only 必须为 false。",
            "- 如果知道官方域名，例如美联储用 federalreserve.gov、美国政府用 .gov、中国政府机构用对应 .gov.cn 域名，必须写入 preferred_domains。",
            "- 对官方事实类问题，preferred_source_kinds 至少包含 official，prefer_primary_sources 必须为 true。",
            "- 对可识别的公司、机构、产品、软件工具、开源项目或品牌介绍，应优先使用官网/官方文档；source_preferences 应包含 official 或 documentation，queries 至少包含 official、官网、about 或 documentation 这类限定词；如果你能确定其官网域名，必须写入 preferred_domains。",
            '- 对"谁是某人/人物简介/企业家/投资人经历"类问题，preferred_source_kinds 应包含 encyclopedia、profile、interview 或 reputable_media，并避开论坛、股吧、SEO 问答、低质聚合页和自媒体搬运。',
            "- 对金融、投资、经济学术语定义或解释类问题，subject.type_hint 应写 finance_term，preferred_source_kinds 应包含 finance_glossary；queries 至少包含一个英文术语 definition 查询。",
            "- 每条 query 不超过 120 个字符。",
            "",
            "输出格式：",
            '{"intent_label":"free text for logs","needs_web":true,"mode":"local_web","subject":{"name":"...","aliases":[],"type_hint":""},"selection_role":"context_only","risk_level":"low","freshness_sensitive":false,"answer_depth":"normal","queries":["..."],"answer_claims_needed":["..."],"claim_requirements":[{"claim_id":"...","required_authority":"primary_or_index"}],"evidence_contract":{"must_cover_subject":true,"minimum_independent_sources":1,"prefer_authoritative_sources":true,"must_use_primary_source":false,"allow_summary_only":true,"answer_policy":"answer_only_supported"},"source_preferences":{"prefer_primary_sources":false,"preferred_source_kinds":[],"preferred_domains":[],"avoid_topics":[]},"answer_contract":{"primary_answer_first":true,"explain_selection_relation":false,"avoid_unasked_side_topics":true},"privacy":{"may_use_selection_text":false,"allowed_context_terms":[]}}',
            "",
            f"当前日期：{payload.get('current_date')}",
            f"模式：{payload.get('mode')}",
            f"用户问题：{payload.get('query')}",
            f"选中文本：{payload.get('selected_text')}",
            f"前文：{payload.get('surrounding_before')}",
            f"后文：{payload.get('surrounding_after')}",
            f"标题路径：{' > '.join(payload.get('heading_path') or [])}",
            f"文章标题：{payload.get('article_title')}",
            f"文章摘要：{payload.get('article_summary')}",
        ]
    )


def _parse_research_plan(
    raw: str | list[str] | dict[str, Any],
    *,
    query: str,
    mode: str,
    selection_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if isinstance(raw, list):
        if not raw:
            return None
        privacy = _sanitize_privacy({"may_use_selection_text": False, "allowed_context_terms": []})
        queries = _sanitize_planned_queries(raw, query=query, selection_context=selection_context, privacy=privacy)
        if not queries:
            return None
        return _research_plan_from_payload({"queries": queries, "needs_web": True}, query=query, mode=mode, selection_context=selection_context)
    if isinstance(raw, dict):
        return _research_plan_from_payload(raw, query=query, mode=mode, selection_context=selection_context)
    text = (raw or "").strip()
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return _parse_research_plan(payload, query=query, mode=mode, selection_context=selection_context)


def _research_plan_from_payload(
    payload: dict[str, Any],
    *,
    query: str,
    mode: str,
    selection_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    candidates = payload.get("queries") or payload.get("query") or payload.get("results") or []
    if isinstance(candidates, str):
        candidates = [candidates]
    if not isinstance(candidates, list):
        candidates = []
    privacy = payload.get("privacy") if isinstance(payload.get("privacy"), dict) else {}
    sanitized_privacy = _sanitize_privacy(privacy)
    queries = _sanitize_planned_queries(
        candidates,
        query=query,
        selection_context=selection_context,
        privacy=sanitized_privacy,
    )
    needs_web = _coerce_bool(payload.get("needs_web"), default=bool(queries))
    if needs_web and not queries:
        return None
    normalized_mode = _normalize_mode(str(payload.get("mode") or mode))
    subject = _sanitize_subject(payload.get("subject") if isinstance(payload.get("subject"), dict) else {}, query=query)
    answer_depth = _enum_value(payload.get("answer_depth"), {"brief", "normal", "detailed"}, "normal")
    queries = _queries_with_detailed_question_anchor(queries, query=query, answer_depth=answer_depth)
    queries = _queries_with_subject_anchor(queries, subject=subject)
    source_preferences = payload.get("source_preferences") if isinstance(payload.get("source_preferences"), dict) else {}
    source_preferences = _source_preferences_with_subject_defaults(source_preferences, subject)
    source_preferences = _source_preferences_with_query_domains(source_preferences, queries)
    raw_evidence_contract = payload.get("evidence_contract") if isinstance(payload.get("evidence_contract"), dict) else {}
    planner_set_minimum_sources = "minimum_independent_sources" in raw_evidence_contract
    evidence_contract = _sanitize_evidence_contract(raw_evidence_contract)
    evidence_contract = _evidence_contract_with_source_preference_defaults(
        evidence_contract,
        source_preferences,
        answer_depth=answer_depth,
        planner_set_minimum_sources=planner_set_minimum_sources,
    )
    answer_contract = payload.get("answer_contract") if isinstance(payload.get("answer_contract"), dict) else {}
    risk_level = _enum_value(payload.get("risk_level"), {"low", "high"}, "low")
    claim_requirements = _sanitize_claim_requirements(payload.get("claim_requirements"))
    answer_claims = _claims_with_stable_subject_claim(
        _string_list(payload.get("answer_claims_needed"))[:8],
        claim_requirements=claim_requirements,
        subject=subject,
        risk_level=risk_level,
    )
    return {
        "intent_label": _clip_text(str(payload.get("intent_label") or "planner"), 80),
        "needs_web": needs_web and normalized_mode in {"local_web", "web_only"},
        "mode": normalized_mode,
        "subject": subject,
        "selection_role": _enum_value(payload.get("selection_role"), {"primary_subject", "context_only", "evidence", "irrelevant", "none"}, "none"),
        "risk_level": risk_level,
        "freshness_sensitive": _coerce_bool(payload.get("freshness_sensitive"), default=False),
        "answer_depth": answer_depth,
        "queries": queries,
        "answer_claims_needed": answer_claims,
        "claim_requirements": claim_requirements,
        "evidence_contract": evidence_contract,
        "source_preferences": _sanitize_source_preferences(source_preferences),
        "answer_contract": _sanitize_answer_contract(answer_contract),
        "privacy": sanitized_privacy,
        "plan_status": "fallback" if not payload else "planned",
    }


def _fallback_research_plan(
    query: str,
    mode: str,
    *,
    selection_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    subject = _fallback_subject_from_selection(query, selection_context)
    return {
        "intent_label": "fallback",
        "needs_web": mode in {"local_web", "web_only"},
        "mode": mode,
        "subject": _sanitize_subject({"name": subject} if subject else {}, query=query),
        "selection_role": "primary_subject" if subject else "none",
        "risk_level": "low",
        "freshness_sensitive": False,
        "answer_depth": "normal",
        "queries": _fallback_queries(query, subject=subject),
        "answer_claims_needed": [],
        "claim_requirements": {},
        "evidence_contract": _sanitize_evidence_contract({}),
        "source_preferences": _sanitize_source_preferences({}),
        "answer_contract": _sanitize_answer_contract({}),
        "privacy": _sanitize_privacy({"may_use_selection_text": False, "allowed_context_terms": []}),
        "plan_status": "fallback",
    }


def _fallback_queries(query: str, *, subject: str = "") -> list[str]:
    compact = _search_query_from_private_context(query)
    candidates = []
    clean_subject = _search_query_from_private_context(subject)
    if clean_subject and clean_subject.lower() not in compact.lower():
        candidates.append(f"{clean_subject} {compact}".strip())
        candidates.append(clean_subject)
    candidates.append(compact)
    for item in list(candidates):
        normalized = _normalize_symbolic_term(item)
        if normalized and normalized.lower() != item.lower():
            candidates.append(normalized)
    return _unique_queries(candidates)[:MAX_WEB_QUERIES]


def _fallback_subject_from_selection(query: str, selection_context: dict[str, Any] | None) -> str:
    if not _query_needs_selected_subject(query):
        return ""
    raw = str((selection_context or {}).get("selected_text") or "").strip()
    if not raw or "\n" in raw or len(raw) > 120:
        return ""
    had_title_wrapper = raw[0:1] in {'《', '"', "'", '\u201c', '\u2018'} and raw[-1:] in {'》', '"', "'", '\u201d', '\u2019'}
    subject = _strip_selection_wrappers(raw)
    if not subject or len(subject) > 100:
        return ""
    if re.search(r"[。！？!?；;]", subject):
        return ""
    if subject.lower() in (query or "").lower():
        return ""
    query_lower = (query or "").lower()
    if any(len(term) >= 2 and term.lower() in query_lower for term in _extract_core_terms(subject)):
        return ""
    if had_title_wrapper or re.search(r"[A-Za-z][A-Za-z0-9 .&'_-]{2,}", subject):
        return subject
    return ""


def _query_needs_selected_subject(query: str) -> bool:
    text = re.sub(r"\s+", "", query or "").lower()
    if not text:
        return False
    return bool(re.search(r"这个|这本|这篇|这段|该|此|它|怎么样|是什么|什么意思|this|it", text, re.I))


def _strip_selection_wrappers(value: str) -> str:
    text = value.strip()
    wrappers = {
        '《': '》',
        '"': '"',
        "'": "'",
        '\u201c': '\u201d',  # ""
        '\u2018': '\u2019',  # ''
        '「': '」',
        '『': '』',
    }
    while len(text) >= 2 and wrappers.get(text[0]) == text[-1]:
        text = text[1:-1].strip()
    return text


def _sanitize_planned_queries(
    items: list[Any],
    *,
    query: str,
    selection_context: dict[str, Any] | None = None,
    privacy: dict[str, Any] | None = None,
) -> list[str]:
    queries = []
    privacy = privacy or _sanitize_privacy({})
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        value = re.sub(r"\s+", " ", value)
        if len(value) > 120:
            value = _search_query_from_private_context(value)
        value = _redact_private_query_context(value, query=query, selection_context=selection_context, privacy=privacy)
        if not value:
            value = _fallback_queries(query)[0] if _fallback_queries(query) else ""
        if not value:
            continue
        queries.append(value[:120])
    return _unique_queries([*_query_anchor_candidates(query, queries), *queries])[:MAX_WEB_QUERIES]


def _query_anchor_candidates(query: str, planned_queries: list[str]) -> list[str]:
    required_terms = _required_query_terms(query)
    if not required_terms:
        return []
    planned_text = " ".join(planned_queries).lower()
    if any(term.lower() in planned_text for term in required_terms):
        return []
    compact = _search_query_from_private_context(query)
    if not compact:
        return []
    anchors: list[str] = []
    if len(compact) <= 80 and not _query_anchor_is_context_only(compact):
        anchors.append(compact)
    anchors.extend(required_terms)
    return _unique_queries([anchor[:120] for anchor in anchors if anchor.strip()])[:2]


def _query_anchor_is_context_only(query: str) -> bool:
    text = re.sub(r"\s+", "", query or "").lower()
    if not text:
        return True
    if _required_query_terms(query):
        return False
    return bool(re.search(r"这个|这篇|这段|这句|这里|上面|文中|该|此|它|其|this|it", text, re.I))


def _queries_with_detailed_question_anchor(queries: list[str], *, query: str, answer_depth: str) -> list[str]:
    if answer_depth != "detailed":
        return queries
    compact = _search_query_from_private_context(query)
    if not compact or len(compact) > 80 or _query_anchor_is_context_only(compact):
        return queries
    if any(compact.lower() == item.lower() for item in queries):
        return queries
    return _unique_queries([compact, *queries])[:MAX_WEB_QUERIES]


def _queries_with_subject_anchor(queries: list[str], *, subject: dict[str, Any]) -> list[str]:
    subject_name = _search_query_from_private_context(str((subject or {}).get("name") or ""))
    if not subject_name:
        return queries
    haystack = " ".join(queries).lower()
    if subject_name.lower() in haystack:
        return queries
    subject_terms = [term for term in _subject_terms("", subject) if len(term.strip()) >= 4]
    if any(term and term.lower() in haystack for term in subject_terms):
        return queries
    aliases = [
        _search_query_from_private_context(str(alias))
        for alias in ((subject or {}).get("aliases") or [])
        if str(alias or "").strip()
    ]
    if any(alias and alias.lower() in haystack for alias in aliases):
        return queries
    first_query = queries[0] if queries else ""
    anchored = f"{subject_name} {first_query}".strip()[:120]
    return _unique_queries([anchored, *queries])[:MAX_WEB_QUERIES]


def _redact_private_query_context(
    value: str,
    *,
    query: str,
    selection_context: dict[str, Any] | None,
    privacy: dict[str, Any],
) -> str:
    if _coerce_bool(privacy.get("may_use_selection_text"), default=False):
        return value
    allowed_terms = {
        str(term or "").strip().lower()
        for term in privacy.get("allowed_context_terms") or []
        if str(term or "").strip()
    }
    user_query = (query or "").lower()
    redacted = value
    for fragment in _private_query_fragments(selection_context or {}):
        normalized = fragment.lower()
        if normalized in allowed_terms or normalized in user_query:
            continue
        redacted = redacted.replace(fragment, " ")
    redacted = re.sub(r"\s+", " ", redacted).strip()
    return redacted


def _private_query_fragments(selection_context: dict[str, Any]) -> list[str]:
    fragments: list[str] = []
    for key in ("selected_text", "surrounding_before", "surrounding_after"):
        text = str(selection_context.get(key) or "").strip()
        if not text:
            continue
        if len(text) <= 120:
            fragments.append(text)
        fragments.extend(part.strip() for part in re.split(r"[。！？；\n.!?;]", text) if len(part.strip()) >= 6)
    for item in selection_context.get("heading_path") or []:
        text = str(item or "").strip()
        if len(text) >= 4:
            fragments.append(text)
    return sorted(_unique(fragments), key=len, reverse=True)


def _source_preferences_with_query_domains(source_preferences: dict[str, Any], queries: list[str]) -> dict[str, Any]:
    updated = dict(source_preferences or {})
    domains = _string_list(updated.get("preferred_domains"))
    for query in queries:
        for match in re.finditer(r"\bsite:([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b", query or "", flags=re.I):
            domains.append(match.group(1).lower().strip("."))
    if domains:
        updated["preferred_domains"] = _unique_queries(domains)
    return updated


def _source_preferences_with_subject_defaults(source_preferences: dict[str, Any], subject: dict[str, Any]) -> dict[str, Any]:
    updated = dict(source_preferences or {})
    if _string_list(updated.get("preferred_source_kinds")):
        return updated
    type_hint = str((subject or {}).get("type_hint") or "").strip().lower()
    if type_hint in {"finance_term", "financial_term", "金融术语", "金融概念"}:
        updated["preferred_source_kinds"] = ["finance_glossary"]
    return updated


def _evidence_contract_with_source_preference_defaults(
    evidence_contract: dict[str, Any],
    source_preferences: dict[str, Any],
    *,
    answer_depth: str = "normal",
    planner_set_minimum_sources: bool = True,
) -> dict[str, Any]:
    updated = dict(evidence_contract or {})
    preferences = _sanitize_source_preferences(source_preferences or {})
    kinds = {str(kind).strip().lower() for kind in _string_list(preferences.get("preferred_source_kinds"))}
    has_specific_source_target = bool(preferences.get("preferred_domains") or preferences.get("prefer_primary_sources"))
    if (
        (not planner_set_minimum_sources or not has_specific_source_target)
        and kinds & get_registry().multi_source_preferred_kinds
        and int(updated.get("minimum_independent_sources") or 1) < 2
    ):
        updated["minimum_independent_sources"] = 2
    if answer_depth == "detailed" and int(updated.get("minimum_independent_sources") or 1) < 2:
        updated["minimum_independent_sources"] = 2
    return updated


def _sanitize_subject(value: dict[str, Any], *, query: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    name = str(value.get("name") or "").strip()
    if not name:
        fallback_terms = _required_query_terms(query)
        name = fallback_terms[0] if fallback_terms else ""
    return {
        "name": _clip_text(name, 120),
        "aliases": _string_list(value.get("aliases"))[:8],
        "type_hint": _clip_text(str(value.get("type_hint") or ""), 80),
    }


def _claims_with_stable_subject_claim(
    claims: list[str],
    *,
    claim_requirements: dict[str, dict[str, str]],
    subject: dict[str, Any],
    risk_level: str,
) -> list[str]:
    normalized = _string_list(claims)[:8]
    if risk_level == "high" or not (subject or {}).get("name") or "subject_overview" in normalized:
        return normalized
    if not normalized or not claim_requirements:
        return normalized
    strict_claims = {
        claim_id
        for claim_id, requirement in claim_requirements.items()
        if requirement.get("required_authority") in {"primary_or_index", "primary_only"}
    }
    if not strict_claims:
        return normalized
    if any(claim not in strict_claims for claim in normalized):
        return normalized
    return _unique_queries(["subject_overview", *normalized])[:8]


def _sanitize_privacy(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "may_use_selection_text": _coerce_bool(value.get("may_use_selection_text"), default=False),
        "allowed_context_terms": _string_list(value.get("allowed_context_terms"))[:12],
    }


def _normalize_symbolic_term(term: str) -> str:
    registry = get_registry()
    normalized = []
    for char in term:
        mapped = registry.greek_letter_name(char)
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


def _subject_terms(query: str, subject: dict[str, Any] | None = None) -> list[str]:
    subject = _sanitize_subject(subject or {}, query=query)
    items = [subject.get("name") or "", *(subject.get("aliases") or [])]
    terms: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        terms.append(text)
        terms.extend(_extract_core_terms(text))
    return _unique_queries([term for term in terms if term])[:12]


def _required_query_terms(query: str) -> list[str]:
    text = re.sub(r"\s+", " ", query or "").strip(" ?？。！!,，")
    if not text:
        return []
    candidates: list[str] = []
    patterns = [
        r"^什么是\s+(.+)$",
        r"^(.+?)\s*(?:是谁|是什么|是什么意思|什么意思|简介|介绍|个人资料)$",
        r"^为什么说\s*(.+?)\s*(?:是|会|成了|成为|属于|叫做).+$",
        r"^为什么\s*(.+?)\s*(?:是|会|成了|成为|属于).+$",
    ]
    texts = _unique_queries([text, re.split(r"[?？。！!]", text, maxsplit=1)[0].strip()])
    for candidate_text in texts:
        for pattern in patterns:
            match = re.match(pattern, candidate_text, flags=re.I)
            if match:
                candidates.append(match.group(1).strip(" ?？。！!,，"))
    terms: list[str] = []
    context_only_terms = {"这", "这个", "该", "此", "它", "其", "this", "it"}
    for candidate in candidates:
        if not candidate:
            continue
        if candidate.lower() in context_only_terms:
            continue
        if re.search(r"[\u4e00-\u9fff]", candidate) and len(candidate) <= 12:
            terms.append(candidate)
            continue
        terms.extend(re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]{1,}", candidate))
    return _unique_queries(terms)[:4]


def _has_required_term_match(required_terms: list[str], matched_terms: list[str]) -> bool:
    matched = {term.lower() for term in matched_terms}
    return any(term.lower() in matched for term in required_terms)


def _source_contains_term(source: dict[str, Any], term: str) -> bool:
    value = str(term or "").strip().lower()
    if not value:
        return False
    haystack = " ".join(
        str(source.get(key) or "")
        for key in ("title", "description", "excerpt", "url")
    ).lower()
    return value in haystack


def _source_title_or_url_contains_term(source: dict[str, Any], term: str) -> bool:
    value = str(term or "").strip().lower()
    if not value:
        return False
    haystack = " ".join(str(source.get(key) or "") for key in ("title", "url")).lower()
    return value in haystack
