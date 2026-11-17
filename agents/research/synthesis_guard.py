"""Fallback answers, citation repair, and source-map helpers for synthesis."""
from __future__ import annotations

import re
from typing import Any

from agents.research.types import MAX_WEB_QUERIES
from agents.research.utils import (
    _claim_requires_observed_metric_value,
    _clip_text,
    _domain,
    _domain_in_list,
    _first_sentence,
    _sanitize_evidence_contract,
    _sanitize_source_preferences,
    _source_has_observed_metric_value,
    _unique,
    _unique_queries,
)


def _fallback_answer(payload: dict[str, Any]) -> str:
    """Generate fallback answer when LLM synthesis fails."""
    refs = _fallback_evidence_refs(payload)
    if refs:
        lines = [f"可确认的是：{_fallback_evidence_sentence(refs)}"]
    else:
        lines = ["无法确认：没有找到足够可靠的公开资料；没有足够可靠的公开资料支撑，无法给出有来源支撑的确定回答。"]
    selection = payload.get("selection_context") or {}
    if selection.get("selected_text"):
        lines.extend(["", "选区：", _clip_text(str(selection.get("selected_text") or ""), 500)])
    if payload.get("local_answer"):
        lines.extend(["", "本地知识库依据：", _clip_text(payload["local_answer"], 1200)])
    if payload.get("web_sources"):
        lines.append("\n可引用材料：")
        for source in payload["web_sources"][:5]:
            lines.append(f"- [{source['ref']}] {source.get('title', '')}: {_clip_text(_fallback_source_text(source), 260)}")
    article = payload.get("article_context") or {}
    if (
        article.get("body")
        and payload.get("mode") != "web_only"
        and not payload.get("local_answer")
        and not payload.get("web_sources")
    ):
        lines.extend(["", "当前文章可提供的上下文：", _clip_text(str(article.get("body") or ""), 600)])
    return "\n".join(lines).strip()


def _fallback_evidence_refs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract evidence references for fallback answer."""
    sources = []
    sources.extend(payload.get("web_sources") or [])
    sources.extend(payload.get("knowledge_sources") or [])
    return [source for source in sources if isinstance(source, dict) and source.get("ref")][:3]


def _fallback_evidence_sentence(sources: list[dict[str, Any]]) -> str:
    """Generate evidence sentence from fallback sources."""
    source = sources[0]
    text = _fallback_source_text(source)
    sentence = _first_sentence(text) or text or str(source.get("title") or "可用来源").strip()
    ref = str(source.get("ref") or "").strip()
    return f"{_clip_text(sentence, 240)} [{ref}]".strip()


def _fallback_source_text(source: dict[str, Any]) -> str:
    """Extract best text from source for fallback."""
    evidence_text = _fallback_best_source_text(source)
    supported = [
        str(item.get("claim_id") or "")
        for item in source.get("claim_support") or []
        if isinstance(item, dict) and item.get("support") == "yes" and str(item.get("claim_id") or "")
    ]
    if not supported:
        supported = [str(item or "") for item in source.get("supports_claims") or [] if str(item or "")]
    if supported:
        text = evidence_text or str(source.get("title") or "可用来源").strip()
        return f"{text}（可支持：{', '.join(_unique_queries(supported)[:4])}）"
    return evidence_text


def _fallback_best_source_text(source: dict[str, Any]) -> str:
    """Select best text field from source for fallback."""
    candidates = [
        ("description", str(source.get("description") or "")),
        ("excerpt", str(source.get("excerpt") or "")),
        ("title", str(source.get("title") or "")),
    ]
    cleaned = [
        (kind, re.sub(r"\s+", " ", item).strip())
        for kind, item in candidates
        if str(item or "").strip()
    ]
    if not cleaned:
        return ""
    return max(cleaned, key=lambda item: _fallback_text_score(item[1], kind=item[0], source=source))[1]


def _fallback_text_score(text: str, *, kind: str = "", source: dict[str, Any] | None = None) -> int:
    """Score text quality for fallback selection."""
    score = min(len(text), 400)
    if "联网搜索结果" in text:
        score += 80
    if re.search(r"定义|是指|是一|原因|因为|分析|解释|衡量|风险|政策|死局|失败", text):
        score += 60
    if re.search(r"下载客户端|登录|无障碍|反馈|新闻 体育 汽车|网易首页|快速导航|ICP备|增值电信业务", text):
        score -= 220
    if kind == "excerpt" and (source or {}).get("extract_status") == "full":
        score += 80
    return score


def _should_use_low_quality_fallback(payload: dict[str, Any]) -> bool:
    """Determine whether to use low-quality fallback based on deterministic metrics."""
    quality = payload.get("search_quality") or {}
    if quality.get("reason") == "not_searched":
        return False
    if payload.get("web_sources"):
        return False
    if payload.get("local_answer") or payload.get("knowledge_sources"):
        return False
    plan = payload.get("research_plan") or {}
    contract = _sanitize_evidence_contract(plan.get("evidence_contract") or {})
    quality_passed = bool(quality.get("passed"))
    if payload.get("mode") == "web_only":
        if _requires_source_backed_fallback(payload):
            return False
        return not quality_passed
    if payload.get("mode") == "local_web":
        has_claims = bool(plan.get("answer_claims_needed"))
        return has_claims and not quality_passed
    return bool(payload.get("rewritten_web_queries") and not quality_passed)


def _requires_source_backed_fallback(payload: dict[str, Any]) -> bool:
    """Check if fallback must be source-backed due to risk or freshness."""
    plan = payload.get("research_plan") or {}
    return bool(plan.get("risk_level") == "high" or plan.get("freshness_sensitive"))


def _low_quality_fallback_answer(payload: dict[str, Any]) -> str:
    """Generate low-quality fallback answer without exposing search diagnostics."""
    lines = ["无法确认：没有找到足够可靠的公开资料；没有足够可靠的公开资料支撑，无法给出有来源支撑的确定回答。"]
    if payload.get("local_answer"):
        refs = [
            str(source.get("ref") or "")
            for source in payload.get("knowledge_sources") or []
            if isinstance(source, dict) and source.get("ref")
        ]
        suffix = f" [{' '.join(refs)}]" if refs else ""
        lines.extend(["", f"本地知识库可确认：{_clip_text(payload['local_answer'], 900)}{suffix}"])
    return "\n".join(lines).strip()


def _cited_refs(answer: str) -> set[str]:
    """Extract cited reference IDs from answer text."""
    return {
        match.upper()
        for match in re.findall(r"\[([KW]\d+)\]", _normalize_tool_citation_brackets(answer), flags=re.IGNORECASE)
    }


def _normalize_tool_citation_brackets(answer: str) -> str:
    """Normalize citation brackets to standard [K1]/[W1] format."""
    text = answer or ""
    text = re.sub(r"\\\[\s*([KW]\d+)\s*\\\]", r"[\1]", text, flags=re.IGNORECASE)
    text = re.sub(r"［\s*([KW]\d+)\s*］", r"[\1]", text, flags=re.IGNORECASE)
    text = re.sub(r"【\s*([KW]\d+)\s*】", r"[\1]", text, flags=re.IGNORECASE)
    return text


def _invalid_non_tool_source_markers(answer: str) -> list[str]:
    """Detect invalid non-tool source markers in answer."""
    markers = []
    for match in re.finditer(r"\[([^\[\]\n]{1,80})\]", answer or ""):
        label = match.group(1).strip()
        if re.fullmatch(r"[KW]\d+", label, flags=re.IGNORECASE):
            continue
        if _looks_like_source_marker(label):
            markers.append(label)
    return _unique(markers)


def _looks_like_source_marker(label: str) -> bool:
    """Check if label looks like a source marker."""
    if "：" in label or ":" in label:
        return True
    return bool(re.search(r"来源|资料|文档|网页|知识库|出处|source|citation|reference", label, re.I))


def _strip_non_tool_source_markers(answer: str, markers: list[str]) -> str:
    """Remove invalid non-tool source markers from answer."""
    marker_set = set(markers)

    def replace(match: re.Match[str]) -> str:
        return "" if match.group(1).strip() in marker_set else match.group(0)

    cleaned = re.sub(r"\[([^\[\]\n]{1,80})\]", replace, answer or "")
    cleaned = re.sub(r"\s+([。！？；，、,.!?;:])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _available_refs(payload: dict[str, Any]) -> set[str]:
    """Collect available reference IDs from knowledge and web sources."""
    refs: set[str] = set()
    for source in payload.get("knowledge_sources") or []:
        if isinstance(source, dict) and source.get("ref"):
            refs.add(str(source["ref"]).upper())
    for source in payload.get("web_sources") or []:
        if isinstance(source, dict) and source.get("ref"):
            refs.add(str(source["ref"]).upper())
    return refs


def _ensure_valid_citations(answer: str, payload: dict[str, Any], warnings: list[str]) -> str:
    """Validate and repair citations in answer."""
    answer = _normalize_tool_citation_brackets(answer)
    invalid_markers = _invalid_non_tool_source_markers(answer)
    if invalid_markers:
        warnings.append(
            f"模型返回了非工具来源标记（{', '.join(invalid_markers[:5])}），已移除这些标记。"
        )
        answer = _strip_non_tool_source_markers(answer, invalid_markers)
    cited_refs = _cited_refs(answer)
    if not cited_refs:
        web_refs = [
            str(source.get("ref") or "").upper()
            for source in payload.get("web_sources") or []
            if isinstance(source, dict) and source.get("ref")
        ]
        if web_refs:
            warnings.append("模型回答未引用已采用的联网来源，已补上可验证来源编号。")
            if _answer_looks_source_refusal(answer):
                return _fallback_answer(payload)
            return _append_ref_to_first_sentence(answer, web_refs[0])
        return answer
    invalid_refs = sorted(cited_refs - _available_refs(payload))
    if not invalid_refs:
        return answer
    repaired = _repair_invalid_citations(answer, payload, invalid_refs)
    if repaired:
        warnings.append(
            f"模型返回了未提供的来源编号（{', '.join(invalid_refs[:5])}），已改用实际可用来源编号。"
        )
        return repaired
    warnings.append(
        f"模型返回了未提供的来源编号（{', '.join(invalid_refs[:5])}），已改用可验证检索摘要。"
    )
    return _fallback_answer(payload)


def _answer_looks_source_refusal(answer: str) -> bool:
    """Check if answer looks like a source refusal."""
    return bool(re.search(r"没有找到足够可靠|资料不足|无法给出有来源支撑|未找到.*可靠|不能.*确定回答", answer or ""))


def _ensure_preferred_domain_citation(answer: str, payload: dict[str, Any]) -> str:
    """Ensure preferred domain sources are cited when available."""
    plan = payload.get("research_plan") or {}
    preferences = _sanitize_source_preferences(plan.get("source_preferences") or {})
    preferred_domains = preferences.get("preferred_domains") or []
    if not preferred_domains:
        return answer
    preferred_refs = [
        str(source.get("ref") or "").upper()
        for source in payload.get("web_sources") or []
        if isinstance(source, dict)
        and source.get("ref")
        and _domain_in_list(str(source.get("domain") or _domain(source.get("url") or "")), preferred_domains)
    ]
    if not preferred_refs or _cited_refs(answer) & set(preferred_refs):
        return answer
    return _append_ref_to_first_sentence(answer, preferred_refs[0])


GENERIC_SOURCE_LABELS = {
    "www",
    "com",
    "cn",
    "org",
    "net",
    "link",
    "source",
    "search",
    "result",
    "results",
    "联网搜索结果",
}


def _ensure_no_unavailable_source_mentions(answer: str, payload: dict[str, Any], warnings: list[str]) -> str:
    """Remove mentions of unavailable sources from answer."""
    adopted_labels = _source_mention_labels(payload.get("web_sources") or [])
    unavailable_labels = _source_mention_labels(payload.get("clue_sources") or [])
    unavailable_labels.update(_source_mention_labels(payload.get("rejected_sources") or []))
    unavailable_labels -= adopted_labels
    mentioned = [
        label
        for label in sorted(unavailable_labels, key=len, reverse=True)
        if _answer_mentions_label(answer, label)
    ]
    if not mentioned:
        return answer
    warnings.append(f"模型回答提到了未采用来源（{', '.join(mentioned[:3])}），已改用可验证检索摘要。")
    return _fallback_answer(payload)


def _ensure_no_unsupported_metric_values(answer: str, payload: dict[str, Any], warnings: list[str]) -> str:
    """Remove unsupported metric values from answer."""
    if _payload_has_supported_metric_claim(payload):
        return answer
    if not _answer_has_metric_value(answer):
        return answer
    cleaned = _strip_metric_value_lines(answer)
    if cleaned and cleaned != answer.strip():
        warnings.append("回答包含未被采用来源支撑的指标数值，已移除相关句子。")
        return cleaned
    warnings.append("回答包含未被采用来源支撑的指标数值，已改用可验证检索摘要。")
    return _strip_metric_value_lines(_fallback_answer(payload)) or _fallback_answer(payload)


def _payload_has_supported_metric_claim(payload: dict[str, Any]) -> bool:
    """Check if payload has supported metric claims."""
    for source in payload.get("web_sources") or []:
        if not isinstance(source, dict):
            continue
        for claim_id in source.get("supports_claims") or []:
            if _claim_requires_observed_metric_value(str(claim_id)) and _source_has_observed_metric_value(source):
                return True
    return False


def _answer_has_metric_value(answer: str) -> bool:
    """Check if answer contains metric values."""
    return any(_line_has_metric_value(line) for line in (answer or "").splitlines())


def _strip_metric_value_lines(answer: str) -> str:
    """Remove lines containing metric values from answer."""
    lines = [line for line in (answer or "").splitlines() if not _line_has_metric_value(line)]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _line_has_metric_value(line: str) -> bool:
    """Check if line contains metric value pattern."""
    text = line or ""
    metric_marker = r"(?:影响因子|impact factor|JCI|JCR|分区|排名|rank(?:ing)?|市值|market cap|Q[1-4])"
    return bool(
        re.search(rf"{metric_marker}.{{0,80}}(?:[$¥￥]?\d|\bQ[1-4]\b)", text, flags=re.I)
        or re.search(rf"(?:[$¥￥]?\d|\bQ[1-4]\b).{{0,80}}{metric_marker}", text, flags=re.I)
    )


def _source_mention_labels(sources: list[dict[str, Any]]) -> set[str]:
    """Extract mention labels from source domains."""
    labels: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        domain = str(source.get("domain") or _domain(source.get("url") or "") or "").lower()
        parts = [part for part in re.split(r"[.-]+", domain) if part]
        if len(parts) >= 2:
            root = parts[-2]
            if len(root) >= 4 and root not in GENERIC_SOURCE_LABELS:
                labels.add(root)
    return labels


def _answer_mentions_label(answer: str, label: str) -> bool:
    """Check if answer mentions a specific label."""
    if not label:
        return False
    if re.fullmatch(r"[A-Za-z0-9_-]+", label):
        return bool(re.search(rf"\b{re.escape(label)}\b", answer or "", flags=re.I))
    return label in (answer or "")


def _append_ref_to_first_sentence(answer: str, ref: str) -> str:
    """Append reference to first sentence of answer."""
    text = (answer or "").strip()
    if not text:
        return text
    marker = f"[{ref}]"
    match = re.search(r"[。！？.!?]", text)
    if not match:
        return f"{text} {marker}".strip()
    index = match.end()
    return f"{text[:index]} {marker}{text[index:]}".strip()


def _repair_invalid_citations(answer: str, payload: dict[str, Any], invalid_refs: list[str]) -> str:
    """Repair invalid citations by replacing with valid sources."""
    plain = re.sub(r"\[[KW]\d+\]", "", answer or "", flags=re.IGNORECASE).strip()
    replacement = _first_repairable_ref(payload, plain)
    if not replacement:
        return ""
    if len(plain) < 20:
        return ""

    invalid = set(invalid_refs)

    def replace(match: re.Match[str]) -> str:
        ref = match.group(1).upper()
        return f"[{replacement}]" if ref in invalid else match.group(0)

    return re.sub(r"\[([KW]\d+)\]", replace, answer or "", flags=re.IGNORECASE)


def _first_repairable_ref(payload: dict[str, Any], answer_text: str) -> str:
    """Find first repairable reference that supports answer."""
    for key in ("web_sources", "knowledge_sources"):
        for source in payload.get(key) or []:
            if isinstance(source, dict) and source.get("ref") and _source_supports_answer(source, answer_text):
                return str(source["ref"]).upper()
    return ""


def _source_supports_answer(source: dict[str, Any], answer_text: str) -> bool:
    """Check if source content supports answer text."""
    evidence = " ".join(
        str(source.get(key) or "")
        for key in ("title", "excerpt", "description", "section")
    ).lower()
    answer = (answer_text or "").lower()
    if not evidence or not answer:
        return False
    cjk_phrases = re.findall(r"[\u4e00-\u9fff]{4,}", answer)
    if any(phrase in evidence for phrase in cjk_phrases):
        return True
    answer_tokens = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", answer)
        if token not in {"the", "and", "for", "with", "that", "this", "into", "from"}
    }
    evidence_tokens = set(re.findall(r"[a-z0-9][a-z0-9_-]{2,}", evidence))
    return len(answer_tokens & evidence_tokens) >= 3


def _filter_cited_sources(sources: list[dict[str, Any]], cited_refs: set[str]) -> list[dict[str, Any]]:
    """Filter sources to only those cited in answer."""
    if not cited_refs:
        return []
    return [
        source
        for source in sources
        if str(source.get("ref") or "").upper() in cited_refs
    ]


def _normalize_cited_sources_and_answer(
    answer: str,
    knowledge_sources: list[dict[str, Any]],
    web_sources: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize citations and filter to cited sources only."""
    cited_refs = _cited_refs(answer)
    cited_knowledge_sources = _filter_cited_sources(knowledge_sources, cited_refs)
    cited_web_sources = _filter_cited_sources(web_sources, cited_refs)
    ref_map: dict[str, str] = {}
    normalized_knowledge = _renumber_sources(cited_knowledge_sources, "K", ref_map)
    normalized_web = _renumber_sources(cited_web_sources, "W", ref_map)
    return _rewrite_answer_refs(answer, ref_map), normalized_knowledge, normalized_web


def _ensure_source_backed_final_answer(
    answer: str,
    payload: dict[str, Any],
    cited_knowledge_sources: list[dict[str, Any]],
    cited_web_sources: list[dict[str, Any]],
    warnings: list[str],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Ensure final answer has source backing when required."""
    if cited_knowledge_sources or cited_web_sources:
        return answer, cited_knowledge_sources, cited_web_sources
    if not _requires_source_backed_final_answer(payload):
        return answer, cited_knowledge_sources, cited_web_sources
    if "没有找到足够可靠" in (answer or ""):
        return answer, cited_knowledge_sources, cited_web_sources
    warnings.append("最终回答缺少可引用来源，已改为证据不足提示。")
    fallback = _low_quality_fallback_answer({**payload, "knowledge_sources": [], "web_sources": []})
    return fallback, [], []


def _requires_source_backed_final_answer(payload: dict[str, Any]) -> bool:
    """Check if final answer requires source backing."""
    mode = str(payload.get("mode") or "")
    if mode == "web_only":
        return True
    if mode != "local_web":
        return False
    return not bool(payload.get("local_answer") or payload.get("knowledge_sources"))


def _renumber_sources(
    sources: list[dict[str, Any]],
    prefix: str,
    ref_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Renumber sources sequentially and build reference map."""
    normalized: list[dict[str, Any]] = []
    for index, source in enumerate(sources, 1):
        old_ref = str(source.get("ref") or f"{prefix}{index}").upper()
        new_ref = f"{prefix}{index}"
        ref_map[old_ref] = new_ref
        item = dict(source)
        item["ref"] = new_ref
        normalized.append(item)
    return normalized


def _rewrite_answer_refs(answer: str, ref_map: dict[str, str]) -> str:
    """Rewrite answer references using reference map."""
    answer = _normalize_tool_citation_brackets(answer)
    if not ref_map:
        return answer

    def replace(match: re.Match[str]) -> str:
        ref = match.group(1).upper()
        return f"[{ref_map.get(ref, ref)}]"

    return re.sub(r"\[([KW]\d+)\]", replace, answer or "", flags=re.IGNORECASE).strip()


def _source_map_item(source: dict[str, Any], source_type: str, index: int) -> dict[str, Any]:
    """Create source map item for response metadata."""
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
        "source_authority": str(source.get("source_authority") or ""),
        "evidence_role": str(source.get("evidence_role") or source.get("source_role") or ""),
        "allowed_usage": source.get("allowed_usage") or [],
        "confidence": str(source.get("confidence") or ""),
    }
