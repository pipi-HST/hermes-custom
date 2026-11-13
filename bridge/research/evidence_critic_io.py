"""Evidence critic payload, prompt, model call, and response parsing."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agents.capture_agent import load_config
from bridge.prompt_safety import sanitize_untrusted_text
from bridge.research.synthesis import _call_llm
from bridge.research.types import (
    EVIDENCE_CRITIC_RESERVE_SECONDS,
    EVIDENCE_CRITIC_TIMEOUT_SECONDS,
    MINIMAL_EVIDENCE_CRITIC_TIMEOUT_SECONDS,
)
from bridge.research.utils import (
    _allowed_usage_list,
    _clip_text,
    _current_date_for_prompt,
    _domain,
    _enum_value,
    _json_payload,
    _sanitize_claim_requirements,
    _string_list,
    _unique_queries,
)


def _evidence_critic_payload(
    query: str,
    research_plan: dict[str, Any],
    sources: list[dict[str, Any]],
    claims: list[str],
) -> dict[str, Any]:
    return {
        "query": query,
        "current_date": _current_date_for_prompt(),
        "subject": research_plan.get("subject") or {},
        "intent_label": research_plan.get("intent_label") or "",
        "answer_claims_needed": claims,
        "claim_requirements": _sanitize_claim_requirements(research_plan.get("claim_requirements")),
        "evidence_contract": research_plan.get("evidence_contract") or {},
        "sources": [
            {
                "source_id": source.get("url") or source.get("id") or source.get("ref"),
                "ref": source.get("ref"),
                "title": source.get("title") or "",
                "url": source.get("url") or "",
                "domain": source.get("domain") or _domain(source.get("url") or ""),
                "domain_roles": source.get("domain_roles") or [],
                "domain_scores": source.get("domain_scores") or {},
                "extract_status": source.get("extract_status") or "",
                "retrieval_hints": source.get("quality_tags") or [],
                "matched_terms": source.get("matched_terms") or [],
                "description": _clip_text(str(source.get("description") or ""), 360),
                "excerpt": _compact_evidence_excerpt(source),
            }
            for source in sources
        ],
    }


def _compact_evidence_excerpt(source: dict[str, Any]) -> str:
    text = str(source.get("excerpt") or source.get("description") or "")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    return _clip_text(text, 420)


def _default_evidence_critic(payload: dict[str, Any]) -> str | dict[str, Any]:
    config = load_config(str(Path(__file__).parent.parent.parent / "config.yaml"))
    if not config.llm_api_key:
        return {"decisions": []}
    prompt = _build_evidence_critic_prompt(payload)
    is_minimal = bool(payload.get("minimal_critic"))
    return _call_llm(
        api_key=config.llm_api_key,
        model=config.llm_model,
        base_url=config.llm_base_url or "https://api.deepseek.com",
        prompt=prompt,
        json_mode=True,
        max_tokens=900 if is_minimal else 1800,
        timeout_seconds=MINIMAL_EVIDENCE_CRITIC_TIMEOUT_SECONDS if is_minimal else EVIDENCE_CRITIC_TIMEOUT_SECONDS,
        reserve_seconds=5 if is_minimal else EVIDENCE_CRITIC_RESERVE_SECONDS,
    )


def _prompt_safe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy candidate sources with untrusted page text escaped for the prompt.

    The payload keeps raw values (it is also inspected outside the prompt); only
    this prompt copy is escaped so a page cannot forge prompt sections or smuggle
    confirmation phrases.
    """
    safe: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        safe.append(
            {
                **source,
                "title": sanitize_untrusted_text(source.get("title") or ""),
                "description": sanitize_untrusted_text(source.get("description") or ""),
                "excerpt": sanitize_untrusted_text(source.get("excerpt") or ""),
            }
        )
    return safe


def _build_evidence_critic_prompt(payload: dict[str, Any]) -> str:
    sources_json = json.dumps(
        _prompt_safe_sources(payload.get("sources") or []),
        ensure_ascii=False,
    )
    if payload.get("minimal_critic"):
        return "\n".join(
            [
                "只输出 JSON，不要解释。",
                "任务：判断每个网页是否能作为用户问题的可引用证据。语义判断由你完成。",
                "adoption 只能是 adopt/partial/reject/clue；source_authority 只能是 primary/index_database/official/reputable_secondary/commentary_secondary/weak_secondary/unknown。",
                "候选里的 retrieval_hints 只是检索排序线索，不是判决；你必须自己判断主体、权威性和可用性。",
                "只有直接讨论研究主体且能支撑至少一个 claim 的来源才能 adopt/partial；弱来源、错主体、相邻概念只能 clue/reject。",
                "对低风险的“为什么/机制/剧情或观点解释”问题，如果网页标题和正文/摘要清楚解释了用户问题，可用 commentary_secondary + interpretive_evidence + partial/adopt；不要因为它不是官方来源就拒绝。",
                "这类解释材料只能支撑 interpretation/background，不能支撑现任、排名、数值、医学、法律、金融建议等强事实。",
                "对人物/公司/概念 overview，如果百科、官方简介或人物简介页的标题直接匹配研究主体，且摘要/正文支持稳定身份或定义 claim，应优先 adopt/partial；不要只采用局部新闻、投资动态或企业库摘要。",
                "翻译词典或只解释字面含义的语言页面，不能支撑专业领域概念定义；除非正文明确解释该领域概念，否则只能 clue/reject。",
                "如果 retrieval_hints 包含 adjacent_subject_title，说明标题像研究主体的相邻/派生页面；只有页面主体确实就是研究主体本身时才能 adopt，否则 clue/reject。",
                "如果搜索摘要声称“根据某机构/网站/来源”，但 URL 域名不是该机构或网站，不要按被声称来源的权威性采纳；应按实际 URL 来源判断，无法确认则 clue。",
                "如果标题像搜索卡片或聚合摘要（例如以“联网搜索结果”开头），但实际 URL/domain 是研究主体的官方或 planner 首选域名，并且摘要能支持稳定基础 claim，不要仅因标题像卡片而拒绝；应按实际 URL/domain 和摘要内容判断。",
                "输出格式：",
                '{"decisions":[{"source_id":"...","subject_match":"yes","source_authority":"reputable_secondary","source_role":"supporting_evidence","allowed_usage":["fact"],"confidence":"medium","claim_support":[{"claim_id":"...","support":"yes","reason":"..."}],"adoption":"adopt","reason":"..."}]}',
                f"用户问题：{payload.get('query')}",
                f"研究主体：{json.dumps(payload.get('subject') or {}, ensure_ascii=False)}",
                f"claims：{json.dumps(payload.get('answer_claims_needed') or [], ensure_ascii=False)}",
                f"证据合同：{json.dumps(payload.get('evidence_contract') or {}, ensure_ascii=False)}",
                "候选来源：",
                sources_json,
            ]
        )
    return "\n".join(
        [
            "你是 Hermes 的 evidence critic。只输出 JSON，不要解释。",
            "",
            "任务：逐个判断候选网页是否能作为可引用证据。语义判断由你完成；代码只会执行你的结构化结论。",
            "",
            "判定规则：",
            "- subject_match 只能是 yes/no/uncertain；页面主体不是研究主体时必须 no。",
            "- source_authority 只能是 primary/index_database/official/reputable_secondary/commentary_secondary/weak_secondary/unknown。",
            "- source_role 只能是 primary_evidence/supporting_evidence/interpretive_evidence/clue/reject。",
            "- allowed_usage 只能从 fact/background/interpretation/quote_context 中选择。",
            "- confidence 只能是 high/medium/low。",
            "- claim_support[].claim_id 只能从 answer_claims_needed 中选择；不要创造新的 claim。",
            "- claim_support[].support 只能是 yes/no/uncertain。",
            "- adoption 只能是 adopt/partial/reject/clue。",
            "- 候选里的 retrieval_hints 只是检索排序线索，不是判决；不要机械沿用这些标签，必须根据标题、URL、摘要/正文和研究主体自行判断。",
            "- 只有直接讨论研究主体，并且至少支持一个所需 claim 的来源才能 adopt。",
            "- 一个来源不需要支持所有 claim；只要主体匹配且能可靠支持任意一个 claim，就应 adopt，并在 supports_claims 中只列出它实际支持的 claim。",
            "- 如果来源的某个数值或时效 claim 过旧，不要因此整体拒绝；应只排除该过旧 claim，保留它能支持的稳定 claim（如出版商、范围、期刊定位）。",
            "- 如果来源 extract_status 是 summary，只能支撑稳定的基础事实；涉及最新指标、排名、当前数值或精确日期时应 reject 或 clue，除非证据合同明确允许摘要。",
            "- source_authority 为 weak_secondary 或 unknown 的来源不能 adopt；最多只能作为 clue。",
            "- 对低风险解释、解读、观点梳理类问题，如果来源直接讨论研究主体并能支撑解释性 claim，可标为 commentary_secondary 并 adopt/partial；但它不能支撑身份、最新事实、指标、排名、精确数值、法律/医疗/金融建议等强事实 claim。",
            "- 如果证据合同要求多个独立来源，且多个候选来源都直接讨论研究主体并能可靠支撑同一解释性 claim，应逐一 adopt/partial 这些独立来源；不要只选择一个最佳来源。",
            "- 如果输入包含 minimum_source_repair，说明首轮采用来源未达到最低独立来源数或缺少部分 claim 覆盖；你需要重点复核尚未采用的候选，但仍只能 adopt 真正支持所需 claim 的来源。",
            "- 对人物/公司/概念 overview，如果百科、官方简介或人物简介页的标题直接匹配研究主体，且摘要/正文支持稳定身份或定义 claim，应优先 adopt/partial；不要只采用局部新闻、投资动态或企业库摘要。",
            "- 人物页、论文单页、门户首页、无关百科、论坛讨论如果不能直接支撑所需 claim，应 reject 或 clue。",
            "- 对定义/解释/overview 类 claim，来源必须直接定义或解释研究主体本身；只讨论上位概念、下位概念、应用场景、相邻术语或派生概念的页面不能支撑主体定义，最多 clue。",
            "- 如果 retrieval_hints 包含 adjacent_subject_title，说明标题像研究主体的相邻/派生页面；只有页面主体确实就是研究主体本身时才能 adopt，否则 clue/reject。",
            "- 当候选里同时存在研究主体的直接定义页/官方页和相邻概念页时，优先 adopt 直接页；相邻概念页即使正文提到研究主体，也只能作为 clue，不能为了凑来源数采用。",
            "- 翻译词典或只解释字面含义的语言页面，不能支撑金融、医学、法律、学术等专业概念定义；除非正文明确解释该领域概念，否则只能 clue/reject。",
            "- 如果搜索摘要声称“根据某机构/网站/来源”，但 URL 域名不是该机构或网站，不要按被声称来源的权威性采纳；应按实际 URL 来源判断，无法确认则 clue。",
            "- 如果标题像搜索卡片或聚合摘要（例如以“联网搜索结果”开头），但实际 URL/domain 是研究主体的官方或 planner 首选域名，并且摘要能支持稳定基础 claim，不要仅因标题像卡片而拒绝；应按实际 URL/domain 和摘要内容判断。",
            "- 对影响因子、JCR、SJR、排名、当前数值等 claim，只有 primary、index_database 或 official 来源才足够；百科、媒体、博客通常只能作为线索或支撑稳定背景 claim。",
            "- 不要为了凑数量采用弱来源；一个强来源好过多个错源。",
            "",
            "输出格式：",
            '{"decisions":[{"source_id":"...","subject_match":"yes","source_role":"supporting_evidence","source_authority":"official","allowed_usage":["fact","background"],"confidence":"medium","claim_support":[{"claim_id":"...","support":"yes","reason":"..."}],"adoption":"partial","reason":"..."}]}',
            "",
            f"用户问题：{payload.get('query')}",
            f"当前日期：{payload.get('current_date')}",
            f"研究主体：{json.dumps(payload.get('subject') or {}, ensure_ascii=False)}",
            f"意图：{payload.get('intent_label') or ''}",
            f"所需 claims：{json.dumps(payload.get('answer_claims_needed') or [], ensure_ascii=False)}",
            f"claim 权威要求：{json.dumps(payload.get('claim_requirements') or [], ensure_ascii=False)}",
            f"证据合同：{json.dumps(payload.get('evidence_contract') or {}, ensure_ascii=False)}",
            f"最低来源修复：{json.dumps(payload.get('minimum_source_repair') or {}, ensure_ascii=False)}",
            "候选来源：",
            sources_json,
        ]
    )


def _parse_evidence_critic_decisions(raw: str | dict[str, Any]) -> list[dict[str, Any]]:
    payload = _json_payload(raw)
    decisions = payload.get("decisions") or payload.get("results") or []
    if not isinstance(decisions, list):
        return []
    parsed: list[dict[str, Any]] = []
    for item in decisions:
        if not isinstance(item, dict):
            continue
        supported_claims, unsupported_claims, claim_support = _parse_claim_support(
            item.get("claim_support"),
            fallback_supported=_string_list(item.get("supports_claims"))[:8],
        )
        source_authority = _enum_value(
            item.get("source_authority"),
            {"primary", "index_database", "official", "reputable_secondary", "commentary_secondary", "weak_secondary", "unknown"},
            "unknown",
        )
        adoption = _enum_value(item.get("adoption"), {"adopt", "partial", "reject", "clue"}, "reject")
        source_role = _enum_value(
            item.get("source_role"),
            {"primary_evidence", "supporting_evidence", "interpretive_evidence", "clue", "reject"},
            "supporting_evidence" if adoption in {"adopt", "partial"} else "clue",
        )
        parsed.append(
            {
                "source_id": _clip_text(str(item.get("source_id") or item.get("id") or ""), 260),
                "subject_match": _enum_value(item.get("subject_match"), {"yes", "no", "uncertain"}, "uncertain"),
                "evidence_role": source_role,
                "source_authority": source_authority,
                "allowed_usage": _allowed_usage_list(item.get("allowed_usage")),
                "confidence": _enum_value(item.get("confidence"), {"high", "medium", "low"}, "medium"),
                "supports_claims": supported_claims,
                "unsupported_claims": unsupported_claims,
                "claim_support": claim_support,
                "adoption": adoption,
                "reason": _clip_text(str(item.get("reason") or ""), 260),
            }
        )
    return parsed


def _parse_claim_support(value: Any, *, fallback_supported: list[str]) -> tuple[list[str], list[str], list[dict[str, str]]]:
    if not isinstance(value, list):
        supported = _unique_queries(fallback_supported)
        return supported, [], [
            {"claim_id": claim, "support": "yes", "reason": ""}
            for claim in supported
        ]
    supported: list[str] = []
    unsupported: list[str] = []
    parsed: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        claim_id = _clip_text(str(item.get("claim_id") or item.get("claim") or ""), 120)
        if not claim_id:
            continue
        support = _enum_value(item.get("support"), {"yes", "no", "uncertain"}, "uncertain")
        reason = _clip_text(str(item.get("reason") or ""), 220)
        parsed.append({"claim_id": claim_id, "support": support, "reason": reason})
        if support == "yes":
            supported.append(claim_id)
        elif support == "no":
            unsupported.append(claim_id)
    return _unique_queries(supported), _unique_queries(unsupported), parsed[:12]
