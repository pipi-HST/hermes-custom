"""Knowledge Card Agent — 两步合成 + 知识关联 + 生命周期管理."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

from agents.garden_card_payload import (
    GARDEN_CARD_SCHEMA_VERSION,
    normalize_garden_payload,
    payload_to_json,
    parse_payload_json,
    project_legacy_fields,
    render_garden_card_body,
)

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = """分析以下知识库内容，提取一张可复用的 Knowledge Card。

用户指定卡片类型：{requested_card_kind}

内容：
{content_text}

输出 JSON（不要 markdown 代码块）：
{{
  "schema_version": "garden_card.v2",
  "card_kind": "viewpoint|fact|method|risk",
  "title": "观点标题（不要和 one_liner 重复，18字以内）",
  "claim": "一句话核心判断。必须能独立复用，不依赖上下文。",
  "confidence": "low|medium|high",
  "when_to_use": ["具体说明以后什么判断场景可以调用这张卡，不要写泛泛而谈的话"],
  "core_judgments": [
    {{
      "id": "j1",
      "statement": "核心判断，不要逐篇罗列材料",
      "reasoning": "为什么这个判断成立",
      "support": [
        {{"summary": "支撑这个判断的依据摘要", "source_ids": ["capture_id"]}}
      ]
    }}
  ],
  "boundaries": ["这张卡不适用的场景、前提或反例"],
  "action_items": ["接受这个判断后应该做什么检查或动作"],
  "source_ids": ["capture_id"]
}}

要求：
- 先提炼一个中心知识对象，再选材料支撑它。
- 如果用户明确指定卡片类型，card_kind 必须遵循用户指定；否则你自己判断最适合的类型。
- card_kind 只能是 viewpoint, fact, method, risk。
- Knowledge Card 不是摘要清单；不要把每篇文章的要点都摊开。
- core_judgments 保留 2-5 条，每条判断要有 reasoning。
- support 只服务于对应判断，不要把所有 quote/case/data 堆在一起。
- when_to_use 保留 2-4 条，必须具体到可执行判断或检查问题，不要写“以后遇到相似问题时可以参考”这类模板句。
- boundaries 保留 1-4 条，说明误用边界或反例。
- action_items 保留 1-4 条，说明可以转成什么检查清单、决策规则或设计动作。
- source_ids 必须对应真实来源，不要把别篇材料的数字或案例合并到同一个来源下。
- 如果依据混合多篇材料，必须拆到不同 support 条目，不要合并成一条无法追溯的证据。
- title 是给人扫视的短标题，claim 是完整判断，两者不要复读。"""

JSON_REPAIR_PROMPT = """下面文本本应是一个 JSON 对象，但格式不合法。

请把它修复成合法 JSON 对象，只返回 JSON，不要 markdown 代码块，不要解释。

原始文本：
{raw_text}
"""

LINK_PROMPT = """为以下新 Knowledge Card 寻找知识关联。

新 Card:
- 一句话: {one_liner}
- 论点: {arguments_text}

已有 Knowledge Cards:
{existing_cards}

判断与哪些已有 Card 有知识关联。输出完整 id 的 JSON 数组：
["garden_card_id_1", "garden_card_id_2"]

只输出 JSON，不要其他内容。没有关联输出 []。"""

INVALID_FILENAME_CHARS = '/:*?"<>|'

CARD_KINDS = {"viewpoint", "fact", "method", "risk"}
CARD_KIND_LABELS = {
    "viewpoint": "观点卡",
    "fact": "事实卡",
    "method": "方法卡",
    "risk": "风险卡",
}


def _normalize_card_kind(value: str | None) -> str:
    kind = str(value or "").strip().lower()
    if kind == "caution":
        return "risk"
    if kind == "question":
        return "viewpoint"
    return kind if kind in CARD_KINDS else "viewpoint"

def _strip_json_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _parse_json_value(raw: str):
    text = _strip_json_fence(raw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        starts = [
            idx for idx in (text.find("{"), text.find("["))
            if idx >= 0
        ]
        for idx in sorted(starts):
            try:
                value, _ = decoder.raw_decode(text[idx:])
                return value
            except json.JSONDecodeError:
                continue
        raise


def _parse_json_object(raw: str) -> dict:
    value = _parse_json_value(raw)
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


class GardenAgent:
    def __init__(self, llm_api_key: str, llm_model: str = "deepseek-v4-pro",
                 llm_base_url: str = "https://api.deepseek.com"):
        self.llm_api_key = llm_api_key
        self.llm_model = llm_model
        self.llm_base_url = llm_base_url

    def synthesize(self, captures: list[dict],
                   existing_garden_cards: list[dict],
                   title: str = "",
                   card_kind: str = "") -> dict:
        """两步合成——返回 v2 payload 和旧字段兼容投影。"""
        if not captures:
            return {"error": "no captures provided"}

        # Step 1: 结构化提取
        content_text = "\n\n---\n\n".join(
            f"[source_id={c.get('id','')}]《{c.get('title','')}》\n{c.get('cached_text','') or c.get('summary','')}"
            for c in captures
        )
        try:
            extracted = self._step1_extract(content_text, card_kind=card_kind)
        except Exception as exc:
            logger.warning("Knowledge Card structured extraction failed: %s", exc)
            return {"error": f"Knowledge Card 结构化提取失败：{exc.__class__.__name__}"}
        resolved_card_kind = (
            _normalize_card_kind(card_kind)
            if card_kind
            else _normalize_card_kind(extracted.get("card_kind"))
        )
        extracted["card_kind"] = resolved_card_kind
        extracted.setdefault("source_ids", [
            str(capture.get("id", "")).strip()
            for capture in captures
            if str(capture.get("id", "")).strip()
        ])

        # Step 2: 知识关联
        links = self._step2_link(extracted, existing_garden_cards)
        links = links + _infer_links_from_shared_sources(captures, existing_garden_cards)
        links = _normalize_knowledge_links(links, existing_garden_cards)

        # 合并生成 v2 payload 和 Markdown
        title = title or self._generate_title(extracted, captures)
        extracted["title"] = title
        extracted["related_card_ids"] = links
        payload = normalize_garden_payload(extracted)
        legacy = project_legacy_fields(payload)
        markdown = self._build_synthesis_markdown(
            title, extracted, links, captures, existing_garden_cards,
            resolved_card_kind,
        )

        return {
            "title": legacy["title"],
            "card_kind": legacy["card_kind"],
            "one_liner": legacy["one_liner"],
            "core_arguments": legacy["core_arguments"],
            "key_materials": legacy["key_materials"],
            "knowledge_links": links,
            "payload": payload,
            "markdown": markdown,
        }

    def _step1_extract(self, content_text: str, card_kind: str = "") -> dict:
        requested = _normalize_card_kind(card_kind) if card_kind else "未指定，由你判断"
        prompt = EXTRACT_PROMPT.format(
            content_text=content_text[:12000],
            requested_card_kind=requested,
        )
        raw = self._call_llm(prompt, max_tokens=6000)
        try:
            return _parse_json_object(raw)
        except Exception:
            repair_prompt = JSON_REPAIR_PROMPT.format(raw_text=raw[:4000])
            repaired = self._call_llm(repair_prompt, max_tokens=6000)
            return _parse_json_object(repaired)

    def _step2_link(self, extracted: dict,
                     existing_cards: list[dict]) -> list[str]:
        if not existing_cards:
            return []
        args_text = "\n".join(
            f"- {text}" for text in _argument_texts(extracted)[:5]
        )
        cards_text = "\n".join(
            f"- id={c['id']} title={c.get('title','')} one_liner={c.get('one_liner','')[:60]}"
            for c in existing_cards[:20]
        )
        prompt = LINK_PROMPT.format(
            one_liner=extracted.get("claim") or extracted.get("one_liner", ""),
            arguments_text=args_text,
            existing_cards=cards_text,
        )
        try:
            raw = self._call_llm(prompt, max_tokens=200)
            value = _parse_json_value(raw)
            return value if isinstance(value, list) else []
        except (json.JSONDecodeError, Exception):
            return []

    def _generate_title(self, extracted: dict, captures: list[dict]) -> str:
        model_title = str(extracted.get("title", "")).strip()
        one_liner = extracted.get("claim") or extracted.get("one_liner", "")
        if model_title and model_title != one_liner:
            return model_title[:48]
        claims = extracted.get("core_judgments") or extracted.get("core_arguments", [])
        for claim in claims:
            text = str(claim.get("statement") or claim.get("claim") or "").strip()
            if text and text != one_liner:
                return text[:24]
        return one_liner[:20] if one_liner else (captures[0].get("title", "")[:20] or "未命名")

    def _build_synthesis_markdown(self, title: str, extracted: dict,
                                   links: list[str],
                                   captures: list[dict],
                                   existing_cards: list[dict],
                                   card_kind: str) -> str:
        payload = normalize_garden_payload({
            **extracted,
            "title": title,
            "card_kind": _normalize_card_kind(card_kind),
            "related_card_ids": links,
            "source_ids": [
                str(capture.get("id", "")).strip()
                for capture in captures
                if str(capture.get("id", "")).strip()
            ],
        })
        if not payload["when_to_use"]:
            payload["when_to_use"] = [_card_use_hint(card_kind)]
        source_links = _capture_links(captures)
        card_lookup = {str(card.get("id", "")): card for card in existing_cards}
        related_links = [_format_knowledge_link(link, card_lookup) for link in links[:8]]
        return render_garden_card_body(
            payload,
            source_links=source_links,
            source_ref_map=_capture_link_map(captures),
            related_links=related_links,
        )

    def _append_source_notes(self, lines: list[str], captures: list[dict]) -> None:
        capture_lookup = _capture_lookup(captures)
        for capture in captures:
            cid = str(capture.get("id", "")).strip()
            source = _format_capture_source(cid, capture_lookup)
            if source:
                lines.append(f"- {source}")
        if not captures:
            lines.append("- （待补充）")

    def _call_llm(self, prompt: str, max_tokens: int = 2000) -> str:
        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{self.llm_base_url}/chat/completions",
                headers=headers, json=body,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]


def _safe_garden_file_stem(title: str, card_id: str) -> str:
    cleaned = "".join(ch for ch in title if ch not in INVALID_FILENAME_CHARS).strip()
    cleaned = cleaned[:48] or "未命名观点"
    return f"{cleaned}-{card_id}"


def _normalize_knowledge_links(links: list[str], existing_cards: list[dict]) -> list[str]:
    card_ids = [
        str(card.get("id", "")).strip()
        for card in existing_cards
        if str(card.get("id", "")).strip()
    ]
    normalized = []
    for raw_link in links or []:
        link = str(raw_link).strip()
        if not link:
            continue
        match = _resolve_card_id(link, card_ids)
        if match and match not in normalized:
            normalized.append(match)
    return normalized


def _infer_links_from_shared_sources(
    captures: list[dict],
    existing_cards: list[dict],
    min_shared: int = 2,
) -> list[str]:
    source_ids = {
        str(capture.get("id", "")).strip()
        for capture in captures
        if str(capture.get("id", "")).strip()
    }
    if len(source_ids) < min_shared:
        return []

    inferred = []
    for card in existing_cards:
        card_id = str(card.get("id", "")).strip()
        card_sources = set(_coerce_json_list(card.get("capture_ids", [])))
        if card_id and len(source_ids & card_sources) >= min_shared:
            inferred.append(card_id)
    return inferred


def _argument_texts(extracted: dict) -> list[str]:
    raw_items = extracted.get("core_judgments") or extracted.get("core_arguments") or []
    texts = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("statement") or item.get("claim") or "").strip()
        if text:
            texts.append(text)
    return texts


def _capture_links(captures: list[dict]) -> list[str]:
    capture_lookup = _capture_lookup(captures)
    links = []
    for capture in captures:
        link = _format_capture_source(str(capture.get("id", "")).strip(), capture_lookup)
        if link and link not in links:
            links.append(link)
    return links


def _capture_link_map(captures: list[dict]) -> dict[str, str]:
    capture_lookup = _capture_lookup(captures)
    result = {}
    for capture in captures:
        capture_id = str(capture.get("id", "")).strip()
        link = _format_capture_source(capture_id, capture_lookup)
        if capture_id and link:
            result[capture_id] = link
            if len(capture_id) >= 8:
                result[capture_id[:8]] = link
    return result


def _coerce_json_list(value) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _resolve_card_id(link: str, card_ids: list[str]) -> str:
    if link in card_ids:
        return link
    matches = [card_id for card_id in card_ids if card_id.startswith(link)]
    return matches[0] if len(matches) == 1 else ""


def _format_knowledge_link(link: str, card_lookup: dict[str, dict]) -> str:
    card = card_lookup.get(str(link).strip())
    if not card:
        return str(link).strip()
    title = str(card.get("title", "")).strip() or str(link).strip()
    target = _safe_garden_file_stem(title, str(link).strip())
    return f"[[garden/{target}|{title}]]"


def _capture_lookup(captures: list[dict]) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for capture in captures:
        cid = str(capture.get("id", "")).strip()
        if not cid:
            continue
        lookup[cid] = capture
        if len(cid) >= 8:
            lookup[cid[:8]] = capture
    return lookup


def _format_capture_source(source_id: str, capture_lookup: dict[str, dict]) -> str:
    source_id = str(source_id or "").strip()
    if not source_id:
        return ""
    capture = capture_lookup.get(source_id)
    if not capture:
        matches = [
            item for key, item in capture_lookup.items()
            if len(source_id) >= 6 and key.startswith(source_id)
        ]
        capture = matches[0] if len(matches) == 1 else None
    if not capture:
        return ""
    title = str(capture.get("title", "")).strip() or source_id
    file_path = str(capture.get("file_path", "")).strip()
    if file_path:
        target = str(Path(file_path).with_suffix(""))
        return f"[[{target}|{title}]]"
    return title


def _card_use_hint(card_kind: str) -> str:
    return {
        "viewpoint": "以后遇到相似问题时，可以把它作为判断和解释框架来调用。",
        "fact": "以后需要案例、数据或背景依据时，可以把它作为事实材料引用。",
        "method": "以后处理相似任务时，可以把它作为可复用做法参考。",
        "risk": "以后做判断或行动前，可以用它提醒自己注意边界和误区。",
    }.get(_normalize_card_kind(card_kind), "以后遇到相似问题时，可以把它作为可复用知识调用。")


def create_garden_card(repo, garden_data: dict) -> str:
    """写入 garden_cards 表，返回 card ID。"""
    card_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    payload = normalize_garden_payload(
        _canonicalize_payload_sources(
            garden_data.get("payload"),
            _json_list(garden_data.get("capture_ids")),
        ),
        legacy=garden_data,
    )
    legacy = project_legacy_fields(payload)
    repo.conn.execute(
        """INSERT INTO garden_cards (id, title, one_liner, card_kind, schema_version,
           payload_json, core_arguments, knowledge_links, key_materials, my_view, status, capture_ids,
           created_at, updated_at, review_interval)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'seed', ?, ?, ?, '1d')""",
        (
            card_id,
            legacy["title"],
            legacy["one_liner"],
            _normalize_card_kind(legacy["card_kind"]),
            GARDEN_CARD_SCHEMA_VERSION,
            payload_to_json(payload),
            json.dumps(legacy["core_arguments"], ensure_ascii=False),
            json.dumps(legacy["knowledge_links"], ensure_ascii=False),
            json.dumps(legacy["key_materials"], ensure_ascii=False),
            legacy["my_view"],
            json.dumps(legacy["capture_ids"], ensure_ascii=False),
            now, now,
        ),
    )
    repo.conn.commit()
    return card_id


def _json_text(value) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def _json_list(text) -> list:
    if isinstance(text, list):
        return text
    try:
        value = json.loads(text or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


def _source_id_lookup(capture_ids: list[str]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    prefix_counts: dict[str, int] = {}
    for raw_id in capture_ids:
        capture_id = str(raw_id or "").strip()
        if not capture_id:
            continue
        lookup[capture_id] = capture_id
        if len(capture_id) >= 8:
            prefix = capture_id[:8]
            prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
            lookup.setdefault(prefix, capture_id)
    return {
        key: value
        for key, value in lookup.items()
        if len(key) != 8 or prefix_counts.get(key, 0) <= 1
    }


def _canonicalize_source_ids(values, lookup: dict[str, str]) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        source_id = str(value or "").strip()
        canonical = lookup.get(source_id, "")
        if canonical and canonical not in result:
            result.append(canonical)
    return result


def _canonicalize_payload_sources(payload, capture_ids: list[str]) -> dict:
    """Make DB-resolved capture IDs authoritative over LLM-emitted short IDs."""
    if not isinstance(payload, dict):
        payload = {}
    canonical_capture_ids = [
        str(capture_id or "").strip()
        for capture_id in capture_ids
        if str(capture_id or "").strip()
    ]
    if not canonical_capture_ids:
        return payload

    lookup = _source_id_lookup(canonical_capture_ids)
    normalized = dict(payload)
    source_ids = _canonicalize_source_ids(
        normalized.get("source_ids") or normalized.get("capture_ids"),
        lookup,
    )
    normalized["source_ids"] = source_ids or canonical_capture_ids

    judgments = []
    for judgment in normalized.get("core_judgments") or []:
        if not isinstance(judgment, dict):
            continue
        next_judgment = dict(judgment)
        support_items = []
        for support in next_judgment.get("support") or []:
            if not isinstance(support, dict):
                continue
            next_support = dict(support)
            mapped = _canonicalize_source_ids(
                next_support.get("source_ids") or [next_support.get("capture_id")],
                lookup,
            )
            if mapped:
                next_support["source_ids"] = mapped
            support_items.append(next_support)
        next_judgment["support"] = support_items
        judgments.append(next_judgment)
    if judgments:
        normalized["core_judgments"] = judgments
    return normalized


def create_garden_draft(repo, garden_data: dict) -> str:
    """写入 garden_drafts 表，返回 draft ID。"""
    draft_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    payload = normalize_garden_payload(
        _canonicalize_payload_sources(
            garden_data.get("payload"),
            _json_list(garden_data.get("capture_ids")),
        ),
        legacy=garden_data,
    )
    legacy = project_legacy_fields(payload)
    repo.conn.execute(
        """INSERT INTO garden_drafts (id, title, one_liner, card_kind, schema_version,
           payload_json, core_arguments, knowledge_links, key_materials, markdown, capture_ids, question_id,
           instruction, status, committed_card_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft_ready', '', ?, ?)""",
        (
            draft_id,
            legacy["title"],
            legacy["one_liner"],
            _normalize_card_kind(legacy["card_kind"]),
            GARDEN_CARD_SCHEMA_VERSION,
            payload_to_json(payload),
            _json_text(legacy["core_arguments"]),
            _json_text(legacy["knowledge_links"]),
            _json_text(legacy["key_materials"]),
            garden_data.get("markdown", ""),
            _json_text(legacy["capture_ids"]),
            garden_data.get("question_id", ""),
            garden_data.get("instruction", ""),
            now,
            now,
        ),
    )
    repo.conn.commit()
    return draft_id


def get_garden_draft(repo, draft_id: str) -> Optional[dict]:
    row = repo.conn.execute(
        "SELECT * FROM garden_drafts WHERE id = ?",
        (draft_id,),
    ).fetchone()
    return dict(row) if row else None


def get_latest_open_garden_draft(repo) -> Optional[dict]:
    row = repo.conn.execute(
        """SELECT * FROM garden_drafts
           WHERE status IN ('draft_ready', 'revision_requested')
           ORDER BY updated_at DESC, created_at DESC
           LIMIT 1"""
    ).fetchone()
    return dict(row) if row else None


def update_garden_draft(
    repo,
    draft_id: str,
    garden_data: dict,
    status: str = "draft_ready",
) -> Optional[dict]:
    existing = get_garden_draft(repo, draft_id)
    if not existing:
        return None

    now = datetime.now(timezone.utc).isoformat()
    payload = normalize_garden_payload(
        _canonicalize_payload_sources(
            garden_data.get("payload"),
            _json_list(garden_data.get("capture_ids") or existing["capture_ids"]),
        ),
        legacy={**existing, **garden_data},
    )
    legacy = project_legacy_fields(payload)
    repo.conn.execute(
        """UPDATE garden_drafts
           SET title=?, one_liner=?, card_kind=?, schema_version=?, payload_json=?,
               core_arguments=?, knowledge_links=?, key_materials=?, markdown=?, capture_ids=?,
               instruction=?, status=?, updated_at=?
           WHERE id=?""",
        (
            legacy["title"],
            legacy["one_liner"],
            _normalize_card_kind(legacy["card_kind"]),
            GARDEN_CARD_SCHEMA_VERSION,
            payload_to_json(payload),
            _json_text(legacy["core_arguments"]),
            _json_text(legacy["knowledge_links"]),
            _json_text(legacy["key_materials"]),
            garden_data.get("markdown", existing["markdown"]),
            _json_text(legacy["capture_ids"]),
            garden_data.get("instruction", existing["instruction"]),
            status,
            now,
            draft_id,
        ),
    )
    repo.conn.commit()
    return get_garden_draft(repo, draft_id)


def commit_garden_draft_to_card(repo, draft_id: str) -> Optional[str]:
    draft = get_garden_draft(repo, draft_id)
    if not draft or draft["status"] == "committed":
        return None

    card_id = create_garden_card(repo, {
        "title": draft["title"],
        "one_liner": draft["one_liner"],
        "card_kind": _normalize_card_kind(draft["card_kind"]),
        "payload": parse_payload_json(draft.get("payload_json")),
        "core_arguments": _json_list(draft["core_arguments"]),
        "knowledge_links": _json_list(draft["knowledge_links"]),
        "key_materials": _json_list(draft["key_materials"]),
        "capture_ids": _json_list(draft["capture_ids"]),
    })
    now = datetime.now(timezone.utc).isoformat()
    repo.conn.execute(
        """UPDATE garden_drafts
           SET status='committed', committed_card_id=?, updated_at=?
           WHERE id=?""",
        (card_id, now, draft_id),
    )
    repo.conn.commit()
    return card_id


def update_garden_card(repo, card_id: str, garden_data: dict) -> Optional[dict]:
    """更新已有 Knowledge Card。"""
    existing = repo.conn.execute(
        "SELECT * FROM garden_cards WHERE id = ?", (card_id,)
    ).fetchone()
    if not existing:
        return None

    now = datetime.now(timezone.utc).isoformat()
    payload = normalize_garden_payload(
        _canonicalize_payload_sources(
            garden_data.get("payload"),
            _json_list(garden_data.get("capture_ids") or existing["capture_ids"]),
        ),
        legacy={**dict(existing), **garden_data},
    )
    legacy = project_legacy_fields(payload)
    # 合并参数（追加新论点, 去重）
    # 简化实现：覆盖更新
    repo.conn.execute(
        """UPDATE garden_cards SET title=?, one_liner=?, card_kind=?, schema_version=?,
           payload_json=?, core_arguments=?, knowledge_links=?, key_materials=?,
           my_view=?, capture_ids=?, updated_at=?, status='seed'
           WHERE id=?""",
        (
            legacy["title"],
            legacy["one_liner"],
            _normalize_card_kind(legacy["card_kind"]),
            GARDEN_CARD_SCHEMA_VERSION,
            payload_to_json(payload),
            json.dumps(legacy["core_arguments"], ensure_ascii=False),
            json.dumps(legacy["knowledge_links"], ensure_ascii=False),
            json.dumps(legacy["key_materials"], ensure_ascii=False),
            legacy["my_view"],
            json.dumps(legacy["capture_ids"], ensure_ascii=False),
            now, card_id,
        ),
    )
    repo.conn.commit()
    return dict(existing)
