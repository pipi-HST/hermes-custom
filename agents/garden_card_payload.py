"""Garden Card v2 payload contract and Markdown renderer."""
from __future__ import annotations

import json
import re
from typing import Any

import yaml

GARDEN_CARD_SCHEMA_VERSION = "garden_card.v2"
LEGACY_GARDEN_CARD_SCHEMA_VERSION = "garden_card.v1"
CARD_KINDS = {"viewpoint", "fact", "method", "risk"}
CONFIDENCE_LEVELS = {"low", "medium", "high"}


def normalize_garden_payload(
    payload: dict[str, Any] | None,
    *,
    legacy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a complete Garden Card v2 payload.

    ``payload`` is authoritative when it already carries v2 fields. ``legacy``
    is used to project existing garden_cards/garden_drafts rows into the same
    shape for old data and compatibility modules.
    """
    payload = payload if isinstance(payload, dict) else {}
    legacy = legacy if isinstance(legacy, dict) else {}

    title = _text(payload.get("title")) or _text(legacy.get("title")) or "未命名卡片"
    claim = _text(payload.get("claim")) or _text(payload.get("one_liner")) or _text(legacy.get("one_liner"))
    card_kind = _normalize_card_kind(payload.get("card_kind") or legacy.get("card_kind"))
    source_ids = _dedupe_texts(
        payload.get("source_ids")
        or payload.get("capture_ids")
        or _json_list(legacy.get("capture_ids"))
        or []
    )
    related_card_ids = _dedupe_texts(
        payload.get("related_card_ids")
        or _json_list(legacy.get("knowledge_links"))
        or []
    )
    key_materials = _list_of_dicts(
        payload.get("key_materials")
        or _json_list(legacy.get("key_materials"))
    )

    core_judgments = _normalize_core_judgments(
        payload.get("core_judgments"),
        fallback_arguments=payload.get("core_arguments") or _json_list(legacy.get("core_arguments")),
        fallback_materials=key_materials,
    )
    if not claim and core_judgments:
        claim = core_judgments[0]["statement"]
    if not claim:
        claim = "（待补充）"

    return {
        "schema_version": GARDEN_CARD_SCHEMA_VERSION,
        "title": title,
        "card_kind": card_kind,
        "claim": claim,
        "content_markdown": _text(payload.get("content_markdown"))
        or _text(legacy.get("content_markdown")),
        "confidence": _normalize_confidence(payload.get("confidence")),
        "when_to_use": _dedupe_texts(
            payload.get("when_to_use")
            or payload.get("use_cases")
            or _lines_from_text(legacy.get("my_view"))
        ),
        "core_judgments": core_judgments,
        "boundaries": _dedupe_texts(payload.get("boundaries")),
        "action_items": _dedupe_texts(payload.get("action_items")),
        "related_card_ids": related_card_ids,
        "source_ids": source_ids,
    }


def parse_payload_json(text: str | None) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def payload_from_row(row: Any) -> dict[str, Any]:
    """Return the normalized v2 payload for a ``garden_cards`` row.

    Only ``payload_json`` and ``capture_ids`` are read; the removed v1 mirror
    columns are never consulted.
    """
    payload = parse_payload_json(_row_value(row, "payload_json", ""))
    if not payload:
        embedded = _row_value(row, "payload", None)
        if isinstance(embedded, dict):
            payload = embedded
    legacy = {"capture_ids": _row_value(row, "capture_ids", "[]")}
    return normalize_garden_payload(payload, legacy=legacy)


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def payload_to_json(payload: dict[str, Any]) -> str:
    return json.dumps(normalize_garden_payload(payload), ensure_ascii=False, sort_keys=True)


def project_legacy_fields(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_garden_payload(payload)
    core_arguments = [
        {
            "claim": item["statement"],
            "consensus": True,
            "source_ids": _support_source_ids(item.get("support") or []),
        }
        for item in normalized["core_judgments"]
    ]
    key_materials = [
        {
            "content": support["summary"],
            "capture_id": (support.get("source_ids") or [""])[0],
        }
        for judgment in normalized["core_judgments"]
        for support in judgment.get("support", [])
        if _text(support.get("summary"))
    ]
    return {
        "title": normalized["title"],
        "one_liner": normalized["claim"],
        "card_kind": normalized["card_kind"],
        "core_arguments": core_arguments,
        "key_materials": key_materials,
        "knowledge_links": normalized["related_card_ids"],
        "my_view": "\n".join(f"- {item}" for item in normalized["action_items"]),
        "capture_ids": normalized["source_ids"],
    }


def render_garden_card_markdown(
    payload: dict[str, Any],
    *,
    status: str = "seed",
    created_at: str = "",
    updated_at: str = "",
    source_links: list[str] | None = None,
    source_ref_map: dict[str, str] | None = None,
    related_links: list[str] | None = None,
    source_theme: str = "",
) -> str:
    card = normalize_garden_payload(payload)
    frontmatter: dict[str, Any] = {
        "type": "garden_card",
        "schema_version": GARDEN_CARD_SCHEMA_VERSION,
        "card_kind": card["card_kind"],
        "status": status,
        "title": card["title"],
        "claim": card["claim"],
        "confidence": card["confidence"],
        "created": created_at,
        "updated": updated_at,
    }
    if source_links:
        frontmatter["sources"] = source_links
    if related_links:
        frontmatter["related"] = related_links
    if source_theme:
        frontmatter["source_theme"] = source_theme

    body = render_garden_card_body(
        card,
        source_links=source_links,
        source_ref_map=source_ref_map,
        related_links=related_links,
    ).rstrip()
    return "\n".join([
        "---",
        yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False).strip(),
        "---",
        "",
        body,
    ]).strip() + "\n"


def render_garden_card_body(
    payload: dict[str, Any],
    *,
    source_links: list[str] | None = None,
    source_ref_map: dict[str, str] | None = None,
    related_links: list[str] | None = None,
) -> str:
    card = normalize_garden_payload(payload)
    lines = [
        f"# {card['title']}",
        "",
        f"> {card['claim']}",
    ]
    _append_list_section(lines, "什么时候用", card["when_to_use"])
    _append_core_judgments(lines, card["core_judgments"])
    _append_why(lines, card["core_judgments"], source_links or [], source_ref_map or {})
    _append_list_section(lines, "边界与反例", card["boundaries"])
    _append_list_section(lines, "行动启发", card["action_items"])
    if source_links:
        lines.extend(["", "## 来源", *[f"- {item}" for item in source_links]])
    if related_links:
        lines.extend(["", "## 关联", *[f"- {item}" for item in related_links]])
    return "\n".join(lines).strip() + "\n"


def _normalize_core_judgments(
    raw: Any,
    *,
    fallback_arguments: list[Any],
    fallback_materials: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    judgments = []
    raw_items = raw if isinstance(raw, list) else []
    if raw_items:
        for index, item in enumerate(raw_items, 1):
            if not isinstance(item, dict):
                continue
            statement = _text(item.get("statement") or item.get("claim"))
            if not statement:
                continue
            judgments.append({
                "id": _text(item.get("id")) or f"j{index}",
                "statement": statement,
                "reasoning": _text(item.get("reasoning")),
                "support": _normalize_support(item.get("support")),
            })
    if judgments:
        return judgments[:5]

    material_by_source = _materials_by_source(fallback_materials)
    for index, item in enumerate(fallback_arguments[:5], 1):
        if isinstance(item, dict):
            statement = _text(item.get("claim") or item.get("statement"))
            source_ids = _dedupe_texts(item.get("source_ids"))
        else:
            statement = _text(item)
            source_ids = []
        if not statement:
            continue
        support = []
        for source_id in source_ids:
            for material in material_by_source.get(source_id, []):
                support.append({
                    "summary": material,
                    "source_ids": [source_id],
                })
        judgments.append({
            "id": f"j{index}",
            "statement": statement,
            "reasoning": "",
            "support": support[:3],
        })
    return judgments


def _normalize_support(raw: Any) -> list[dict[str, Any]]:
    items = raw if isinstance(raw, list) else []
    support = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        summary = _text(item.get("summary") or item.get("content"))
        if not summary:
            continue
        support.append({
            "summary": summary,
            "source_ids": _dedupe_texts(item.get("source_ids") or [item.get("capture_id")]),
        })
    return support


def _materials_by_source(materials: list[dict[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for material in materials:
        source_id = _text(material.get("capture_id") or material.get("source_id"))
        content = _text(material.get("content") or material.get("summary"))
        if not source_id or not content:
            continue
        result.setdefault(source_id, []).append(content)
    return result


def _append_list_section(lines: list[str], title: str, items: list[str]) -> None:
    if not items:
        return
    lines.extend(["", f"## {title}", *[f"- {item}" for item in items]])


def _append_core_judgments(lines: list[str], judgments: list[dict[str, Any]]) -> None:
    if not judgments:
        return
    lines.extend(["", "## 核心判断"])
    for index, item in enumerate(judgments, 1):
        lines.append(f"{index}. {item['statement']}")


def _append_why(
    lines: list[str],
    judgments: list[dict[str, Any]],
    source_links: list[str],
    source_ref_map: dict[str, str],
) -> None:
    if not judgments:
        return
    lines.extend(["", "## 为什么成立"])
    source_lookup = {
        **{str(index + 1): link for index, link in enumerate(source_links)},
        **source_ref_map,
    }
    for index, item in enumerate(judgments, 1):
        lines.extend(["", f"### {index}. {item['statement']}"])
        if item.get("reasoning"):
            lines.append(f"- 理由：{item['reasoning']}")
        for support in item.get("support", []):
            lines.append(f"- 依据：{support['summary']}")
            linked = [
                source_lookup.get(source_id, source_id)
                for source_id in support.get("source_ids", [])
                if source_id
            ]
            if linked:
                lines.append(f"- 来源：{'、'.join(linked)}")


def _support_source_ids(support: list[dict[str, Any]]) -> list[str]:
    return _dedupe_texts(
        source_id
        for item in support
        for source_id in item.get("source_ids", [])
    )


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
    return value if isinstance(value, list) else []


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in _json_list(value) if isinstance(item, dict)]


def _dedupe_texts(value: Any) -> list[str]:
    seen = set()
    result = []
    values = value if isinstance(value, list) else list(value) if not isinstance(value, (str, bytes, dict, type(None))) else [value]
    for item in values:
        text = _text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _lines_from_text(value: Any) -> list[str]:
    text = _text(value)
    if not text:
        return []
    return [
        re.sub(r"^\s*[-*]\s*", "", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]


_CARD_KIND_SYNONYMS = {
    "risk": "risk",
    "caution": "risk",
    "pitfall": "risk",
    "风险": "risk",
    "边界": "risk",
    "避坑": "risk",
    "踩坑": "risk",
    "风险边界": "risk",
    "避坑边界": "risk",
    "viewpoint": "viewpoint",
    "insight": "viewpoint",
    "quote": "viewpoint",
    "洞察": "viewpoint",
    "金句": "viewpoint",
    "观点": "viewpoint",
    "洞察金句": "viewpoint",
    "method": "method",
    "framework": "method",
    "方法": "method",
    "框架": "method",
    "方法论": "method",
    "方法框架": "method",
    "fact": "fact",
    "concept": "fact",
    "新知": "fact",
    "概念": "fact",
    "事实": "fact",
    "新知概念": "fact",
}
_CARD_KIND_SEPARATORS = re.compile(r"[\s/|｜·、,，]+")


def _normalize_card_kind(value: Any) -> str:
    kind = _text(value).lower()
    compact = _CARD_KIND_SEPARATORS.sub("", kind)
    if compact in _CARD_KIND_SYNONYMS:
        return _CARD_KIND_SYNONYMS[compact]
    if kind in _CARD_KIND_SYNONYMS:
        return _CARD_KIND_SYNONYMS[kind]
    if kind == "question":
        return "viewpoint"
    return kind if kind in CARD_KINDS else "viewpoint"


def _normalize_confidence(value: Any) -> str:
    confidence = _text(value).lower()
    return confidence if confidence in CONFIDENCE_LEVELS else "medium"


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
