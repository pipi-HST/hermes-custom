"""Answer synthesis, fallback generation, and citation management."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from agents.capture_agent import load_config
from bridge.prompt_safety import sanitize_untrusted_text
from bridge.research.types import (
    MAX_DYNAMIC_EXTRACT_URLS,
    MAX_WEB_QUERIES,
    SYNTHESIS_LLM_TIMEOUT_SECONDS,
)
from bridge.prompt_contracts import MODEL_SUPPLEMENT_CONTRACT
from bridge.research.utils import (
    _clip_text,
    _current_date_for_prompt,
    _domain,
    _domain_in_list,
    _first_sentence,
    _sanitize_answer_contract,
    _sanitize_evidence_contract,
    _sanitize_source_preferences,
    _claim_requires_observed_metric_value,
    _source_has_observed_metric_value,
    _research_budget_available,
    _stage_timeout,
    _unique,
    _unique_queries,
)
from bridge.research.search import _independent_source_count
from bridge.research.synthesis_guard import (
    GENERIC_SOURCE_LABELS,
    _answer_has_metric_value,
    _answer_looks_source_refusal,
    _answer_mentions_label,
    _append_ref_to_first_sentence,
    _available_refs,
    _cited_refs,
    _ensure_no_unavailable_source_mentions,
    _ensure_no_unsupported_metric_values,
    _ensure_preferred_domain_citation,
    _ensure_source_backed_final_answer,
    _ensure_valid_citations,
    _fallback_answer,
    _fallback_best_source_text,
    _fallback_evidence_refs,
    _fallback_evidence_sentence,
    _fallback_source_text,
    _fallback_text_score,
    _filter_cited_sources,
    _first_repairable_ref,
    _invalid_non_tool_source_markers,
    _line_has_metric_value,
    _looks_like_source_marker,
    _low_quality_fallback_answer,
    _normalize_cited_sources_and_answer,
    _normalize_tool_citation_brackets,
    _payload_has_supported_metric_claim,
    _renumber_sources,
    _repair_invalid_citations,
    _requires_source_backed_fallback,
    _requires_source_backed_final_answer,
    _rewrite_answer_refs,
    _should_use_low_quality_fallback,
    _source_map_item,
    _source_mention_labels,
    _source_supports_answer,
    _strip_metric_value_lines,
    _strip_non_tool_source_markers,
)


def _synthesize_answer(
    payload: dict[str, Any],
    warnings: list[str],
    *,
    timeout_seconds: float | None = None,
) -> str:
    """Generate answer using LLM or fallback to structured summary."""
    config = load_config(str(Path(__file__).parent.parent.parent / "config.yaml"))
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
            max_tokens=_synthesis_max_tokens(payload),
            timeout_seconds=timeout_seconds or SYNTHESIS_LLM_TIMEOUT_SECONDS,
            reserve_seconds=0,
        )
    except Exception as exc:
        warnings.append(f"综合回答生成失败，已返回检索材料摘要：{exc}")
        return _fallback_answer(payload)


def _build_synthesis_prompt(payload: dict[str, Any]) -> str:
    """Build synthesis prompt with evidence contracts and source context."""
    article = payload.get("article_context") or {}
    selection = payload.get("selection_context") or {}
    plan = payload.get("research_plan") or {}
    light_path = plan.get("path") == "light_web_qa"
    answer_contract = _sanitize_answer_contract(plan.get("answer_contract") or {})
    evidence_contract = _sanitize_evidence_contract(plan.get("evidence_contract") or {})
    parts = [
        f"用户问题：{payload['query']}",
        f"研究模式：{payload['mode']}",
        f"当前日期：{_current_date_for_prompt()}",
        f"Planner 标签：{plan.get('intent_label') or '未提供'}",
        f"研究主体：{json.dumps(plan.get('subject') or {}, ensure_ascii=False)}",
        f"证据合同：{json.dumps(evidence_contract, ensure_ascii=False)}",
        f"需要被来源支撑的 claims：{json.dumps(plan.get('answer_claims_needed') or [], ensure_ascii=False)}",
        f"回答深度：{plan.get('answer_depth') or 'normal'}",
        f"选区角色：{plan.get('selection_role') or 'none'}",
        f"来源偏好：{json.dumps(plan.get('source_preferences') or {}, ensure_ascii=False)}",
        f"回答合同：{json.dumps(answer_contract, ensure_ascii=False)}",
        "",
        "回答要求：",
        "- 用中文回答，先给结论，再分层说明依据。",
        "- 需要引用本地知识库时，只能使用提供的 [K1] [K2] ... 编号。",
        "- 需要引用联网资料时，只能使用提供的 [W1] [W2] ... 编号。",
        "- 方括号来源标记只允许使用上述 [K数字]/[W数字]；不要写 [文档目录：...]、[来源：...] 或其他自定义来源标签。",
        f"- {MODEL_SUPPLEMENT_CONTRACT}",
        "- 不要编造来源；没有依据时明确说不确定。",
        "- 如果官方/原始来源与媒体、百科、博客等二手来源冲突，必须以官方/原始来源为准；除非有更新的官方来源证明变更，不能用二手报道覆盖官方来源。",
        '- 回答"现任/当前/现在"类职位问题时，官方机构页面、任命公告、政府公告优先级最高；媒体报道只能作为补充，不能推翻官方来源。',
        '- 不要在回答正文末尾生成"参考来源"或来源清单；来源由界面统一展示在最底部。',
        "- 当前文章和选区用于理解用户指代；除非用户明确要求比较文章内容，否则不要展开文章里的旁支主题。",
        '- 主答案不是检索报告。禁止使用"从搜索结果看"、"目前搜索结果"、"本次搜索"、"未找到官方"、"以上信息来自"、"建议进一步查阅"这类展示检索过程或自我反思的表述。',
        "- 来源强弱用于控制结论力度，不要写成免责声明。如果只能得到二手或摘要材料，直接降低确定性或说明无法确认官方定义，但仍要先回答用户问的内容。",
    ]
    if light_path:
        parts.extend(
            [
                "- 当前是轻量联网答疑路径：可以综合已提供的网页正文和搜索摘要，但必须引用实际提供的 [W] 来源。",
                "- 搜索摘要可以作为有限材料使用；如果来源主要是摘要或二手解读，不要输出检索过程说明，只通过谨慎措辞体现边界。",
                "- 不要因为来源不是审计级证据就拒绝回答普通解释题；但不要把医疗、法律、金融等高风险问题包装成确定建议。",
            ]
        )
    else:
        parts.extend(
            [
                "- 只能使用通过证据合同的 K/W 来源回答；不要引用候选、线索或被拒绝来源。",
                "- 不要补充 K/W 来源没有直接支持的排名、指标、现任职位、最新事实或精确数值。",
                "- 如果联网来源带有 supports_claims，只能用该来源支撑这些列出的 claim；未列出的 claim 必须说未确认或不写。",
            ]
        )
    if payload.get("mode") == "web_only":
        parts.append("- 当前是纯联网模式：主要依据必须来自联网资料，不要把当前文章正文当作主依据。")
    if plan.get("risk_level") == "high" or plan.get("freshness_sensitive"):
        parts.append("- Planner 标记该问题需要更严格的证据边界；优先依据原始来源，并明确不确定性。")
    evidence_status = (payload.get("search_quality") or {}).get("evidence_status")
    if evidence_status == "limited":
        parts.append("- 当前可采用证据有限：可以解释已有来源直接支持的内容，但不要写免责声明；把限制体现为更谨慎的结论，不补充未被来源支撑的强事实。")
    if plan.get("answer_depth") == "brief":
        parts.append("- 回答保持简洁，适合定义或简单事实。")
    elif (plan.get("answer_depth") or "normal") == "normal" and evidence_status != "insufficient":
        parts.append("- 回答不要只写一句话：至少包含一个直接结论，并用 2-4 个简洁要点说明定义、理解方式、常见用途或边界。")
    elif plan.get("answer_depth") == "detailed" and evidence_status != "insufficient":
        parts.append("- 用户需要较完整解释：在不越过来源支持范围的前提下，给出结构化、分层、相对充分的回答；通常包含直接结论、核心原因、机制链条、限制或争议这几类信息。")
    if answer_contract["primary_answer_first"]:
        parts.append("- 先直接回答用户的主问题，再展开依据。")
    if answer_contract["explain_selection_relation"] and selection.get("selected_text"):
        parts.append("- 说明选区和主问题的关系，但不要把选区自动当成主问题。")
    if selection.get("selected_text"):
        parts.append("- 如果用户选区是短词、术语或英文片段，必须结合选区前文、后文、标题路径和当前文章解释它在本文中的含义；不要只给通用词典释义。")
    if answer_contract["avoid_unasked_side_topics"]:
        parts.append("- 不展开用户没有要求的旁支。")
    if article and payload.get("mode") != "web_only":
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
            ]
        )
        if payload.get("mode") != "web_only":
            parts.extend(
                [
                    f"前文：\n{_clip_text(str(selection.get('surrounding_before') or ''), 1500)}",
                    f"后文：\n{_clip_text(str(selection.get('surrounding_after') or ''), 1500)}",
                ]
            )
    if payload.get("local_answer"):
        parts.extend(["", "本地知识库回答草稿：", _clip_text(payload["local_answer"], 6000)])
    if payload.get("knowledge_sources"):
        parts.append("\n本地知识库来源：")
        for source in payload["knowledge_sources"]:
            title = sanitize_untrusted_text(source.get("title", ""))
            section = sanitize_untrusted_text(source.get("section", ""))
            parts.append(f"[{source['ref']}] {title} {section}".strip())
    if payload.get("web_sources"):
        parts.append("\n联网资料：")
        for source in payload["web_sources"]:
            usage = ",".join(source.get("allowed_usage") or [])
            role = source.get("evidence_role") or source.get("source_role") or ""
            # Web titles/snippets/bodies are untrusted: escape them so they can
            # never impersonate prompt sections.  Contracts/plan JSON above is
            # left untouched on purpose.
            title = sanitize_untrusted_text(source.get("title", ""))
            excerpt = sanitize_untrusted_text(
                _clip_text(source.get("excerpt", "") or source.get("description", ""), 1800)
            )
            parts.append(
                f"[{source['ref']}] {title}\n"
                f"URL: {source.get('url', '')}\n"
                f"证据角色：{role}; 权威类型：{source.get('source_authority') or ''}; 可用于：{usage or '未标注'}; 置信度：{source.get('confidence') or ''}\n"
                f"支持的 claims：{json.dumps(source.get('supports_claims') or [], ensure_ascii=False)}\n"
                f"不支持的 claims：{json.dumps(source.get('unsupported_claims') or [], ensure_ascii=False)}\n"
                f"逐 claim 裁判：{json.dumps(source.get('claim_support') or [], ensure_ascii=False)}\n"
                f"摘录：{excerpt}"
            )
    return "\n".join(parts)


def _synthesis_max_tokens(payload: dict[str, Any]) -> int:
    """Determine max tokens based on answer depth."""
    plan = payload.get("research_plan") or {}
    if plan.get("answer_depth") == "detailed":
        return 5200
    if plan.get("answer_depth") == "brief":
        return 2200
    return 3600


def _call_llm(
    *,
    api_key: str,
    model: str,
    base_url: str,
    prompt: str,
    json_mode: bool = False,
    max_tokens: int = 3600,
    timeout_seconds: float = 120,
    reserve_seconds: float = 0,
) -> str:
    """Call LLM API with timeout and budget management."""
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
        "max_tokens": max(1200, min(int(max_tokens or 3600), 6000)),
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    stage_timeout = _stage_timeout(float(timeout_seconds or 120), reserve_seconds=reserve_seconds)
    if stage_timeout < 1.0:
        raise TimeoutError("research budget exhausted before LLM request")
    with httpx.Client(timeout=max(1.0, stage_timeout)) as client:
        resp = client.post(f"{base_url}/chat/completions", headers=headers, json=body)
        if json_mode and resp.status_code in {400, 422}:
            if not _research_budget_available(1.0):
                raise TimeoutError("research budget exhausted before LLM JSON retry")
            body.pop("response_format", None)
            resp = client.post(f"{base_url}/chat/completions", headers=headers, json=body)
        resp.raise_for_status()
        payload = resp.json()
    return str(payload["choices"][0]["message"]["content"]).strip()


def _trace_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract trace information from sources for research trace."""
    traced: list[dict[str, Any]] = []
    for source in (sources or [])[:MAX_DYNAMIC_EXTRACT_URLS]:
        if not isinstance(source, dict):
            continue
        traced.append(
            {
                "ref": source.get("ref"),
                "title": source.get("title") or "",
                "url": source.get("url") or "",
                "domain": source.get("domain") or _domain(source.get("url") or ""),
                "search_backend": source.get("search_backend") or "",
                "fallback_candidate": bool(source.get("fallback_candidate")),
                "extract_status": source.get("extract_status"),
                "rank_score": source.get("rank_score"),
                "source_role": source.get("source_role"),
                "evidence_role": source.get("evidence_role"),
                "source_score": source.get("source_score"),
                "quality_reason": source.get("quality_reason"),
                "entity_match": source.get("entity_match"),
                "quality_tags": source.get("quality_tags") or [],
                "supports_claims": source.get("supports_claims") or [],
                "unsupported_claims": source.get("unsupported_claims") or [],
                "claim_support": source.get("claim_support") or [],
                "source_authority": source.get("source_authority") or "",
                "allowed_usage": source.get("allowed_usage") or [],
                "confidence": source.get("confidence") or "",
                "evidence_critic_reason": source.get("evidence_critic_reason") or "",
            }
        )
    return traced


def _build_research_trace(payload: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """Build comprehensive research trace for response metadata."""
    plan = payload.get("research_plan") or {}
    provider_status = payload.get("provider_status") or {}
    stage_timings = provider_status.get("stage_timings") or {}
    quality = payload.get("search_quality") or {}
    retrieval_health = provider_status.get("retrieval_health") or quality.get("retrieval_health") or {}
    web_queries = payload.get("web_queries") or []
    rewritten_queries = payload.get("rewritten_web_queries") or []
    web_source_trace = [
        {
            "ref": source.get("ref"),
            "title": source.get("title"),
            "url": source.get("url"),
            "domain": source.get("domain"),
            "extract_status": source.get("extract_status"),
            "rank_score": source.get("rank_score"),
            "source_role": source.get("source_role"),
            "evidence_role": source.get("evidence_role"),
            "source_score": source.get("source_score"),
            "quality_reason": source.get("quality_reason"),
            "entity_match": source.get("entity_match"),
            "quality_tags": source.get("quality_tags") or [],
            "source_authority": source.get("source_authority") or "",
            "allowed_usage": source.get("allowed_usage") or [],
            "confidence": source.get("confidence") or "",
            "supports_claims": source.get("supports_claims") or [],
        }
        for source in (payload.get("web_sources") or [])[:MAX_DYNAMIC_EXTRACT_URLS]
        if isinstance(source, dict)
    ]
    candidate_sources = _trace_sources(payload.get("retrieved_web_sources") or [])
    clue_sources = _trace_sources(payload.get("clue_sources") or [])
    rejected_sources = _trace_sources(payload.get("rejected_sources") or [])
    return {
        "path": plan.get("path") or provider_status.get("path") or "legacy_research",
        "legacy_research": bool(plan.get("legacy_research", True)),
        "mode": payload.get("mode"),
        "subject": plan.get("subject") or {},
        "evidence_contract": plan.get("evidence_contract") or {},
        "plan_status": payload.get("plan_status"),
        "planner_error": plan.get("planner_error") or "",
        "planner_elapsed_ms": plan.get("planner_elapsed_ms") or 0,
        "planner_raw_preview": plan.get("planner_raw_preview") or "",
        "planner_parse_error": plan.get("planner_parse_error") or "",
        "planner_fallback_reason": plan.get("planner_fallback_reason") or "",
        "intent_label": plan.get("intent_label"),
        "query_intent": plan.get("intent_label"),
        "needs_web": plan.get("needs_web"),
        "web_search_attempted": bool(plan.get("needs_web")),
        "selection_role": plan.get("selection_role"),
        "answer_depth": plan.get("answer_depth") or "normal",
        "queries": web_queries,
        "web_queries": web_queries,
        "rewritten_queries": rewritten_queries,
        "rewritten_web_queries": rewritten_queries,
        "search_backend": provider_status.get("search_backend") or "",
        "extract_backend": provider_status.get("extract_backend") or "",
        "provider_status": provider_status,
        "stage_timings": stage_timings,
        "retrieval_health": retrieval_health,
        "fallback_attempts": provider_status.get("fallback_attempts") or [],
        "fallback_search": bool(provider_status.get("fallback_search") or provider_status.get("fallback_attempts")),
        "search_quality": quality,
        "sources": web_source_trace,
        "web_sources": web_source_trace,
        "candidate_sources": candidate_sources,
        "retrieved_web_sources": candidate_sources,
        "evidence_sources": web_source_trace,
        "clue_sources": clue_sources,
        "rejected_sources": rejected_sources,
        "source_counts": {
            "candidate": len(candidate_sources),
            "evidence": len(web_source_trace),
            "adopted": len(web_source_trace),
            "clue": len(clue_sources),
            "rejected": len(rejected_sources),
            "independent_web": _independent_source_count(payload.get("web_sources") or []),
            "adopted_independent_domains": _independent_source_count(payload.get("web_sources") or []),
            "candidate_independent_domains": _independent_source_count(payload.get("retrieved_web_sources") or []),
            "retrieval_candidate": int((retrieval_health or {}).get("candidate_count") or 0),
            "retrieval_independent_domains": int((retrieval_health or {}).get("independent_domain_count") or 0),
        },
        "candidate_count": int(provider_status.get("candidate_count") or len(candidate_sources)),
        "read_pages": int(provider_status.get("read_pages") or 0),
        "summary_sources": int(provider_status.get("summary_only_pages") or 0),
        "used_sources": int(provider_status.get("used_sources") or len(web_source_trace)),
        "evidence_status": quality.get("evidence_status") or ("sufficient" if quality.get("passed") else "insufficient"),
        "warnings": _unique(list(provider_status.get("warnings") or []) + list(warnings)),
    }
