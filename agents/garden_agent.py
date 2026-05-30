"""Garden Agent — 两步合成 + 知识关联 + 生命周期管理."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = """分析以下知识库内容，提取一张可复用的 Knowledge Card。

用户指定卡片类型：{requested_card_kind}

内容：
{content_text}

输出 JSON（不要 markdown 代码块）：
{{
  "card_kind": "viewpoint|fact|method|caution|question",
  "title": "观点标题（不要和 one_liner 重复，18字以内）",
  "one_liner": "一句话说明这张卡的核心内容。可以是观点、事实、方法、风险或问题。",
  "core_arguments": [
    {{"claim": "支撑这张卡的关键理由、步骤、表现或线索，不要逐篇罗列材料", "consensus": true|false, "source_ids": ["capture_id"]}}
  ],
  "key_materials": [
    {{"type": "quote|data|case", "content": "最能支撑判断的依据，不要收集所有材料", "capture_id": "xxx"}}
  ]
}}

要求：
- 先提炼一个中心知识对象，再选材料支撑它。
- 如果用户明确指定卡片类型，card_kind 必须遵循用户指定；否则你自己判断最适合的类型。
- card_kind 只能是 viewpoint, fact, method, caution, question。
- Garden Card 不是摘要清单；不要把每篇文章的要点都摊开。
- core_arguments 保留 3-5 条，key_materials 保留 3-5 条。
- 每条 key_materials 只能来自一篇 capture；如果依据混合多篇材料，必须拆成多条。
- capture_id 必须对应这条 key_materials 的真实来源，不要把别篇材料的数字或案例合并到同一个来源下。
- title 是给人扫视的短标题，one_liner 是完整判断，两者不要复读。"""

JSON_REPAIR_PROMPT = """下面文本本应是一个 JSON 对象，但格式不合法。

请把它修复成合法 JSON 对象，只返回 JSON，不要 markdown 代码块，不要解释。

原始文本：
{raw_text}
"""

LINK_PROMPT = """为以下新 Garden Card 寻找知识关联。

新 Card:
- 一句话: {one_liner}
- 论点: {arguments_text}

已有 Garden Cards:
{existing_cards}

判断与哪些已有 Card 有知识关联。输出完整 id 的 JSON 数组：
["garden_card_id_1", "garden_card_id_2"]

只输出 JSON，不要其他内容。没有关联输出 []。"""

INVALID_FILENAME_CHARS = '/:*?"<>|'

CARD_KINDS = {"viewpoint", "fact", "method", "caution", "question"}
CARD_KIND_LABELS = {
    "viewpoint": "观点卡",
    "fact": "事实卡",
    "method": "方法卡",
    "caution": "注意卡",
    "question": "问题卡",
}
CARD_KIND_HEADINGS = {
    "viewpoint": ("核心观点", "为什么这样看", "依据", "反例或边界", "关联"),
    "fact": ("事实", "为什么重要", "来源依据", "可用场景", "关联"),
    "method": ("适用场景", "做法", "注意点", "来源依据", "关联"),
    "caution": ("风险", "典型表现", "怎么避免", "来源依据", "关联"),
    "question": ("问题", "为什么值得追踪", "已有线索", "下一步观察", "关联"),
}


def _normalize_card_kind(value: str | None) -> str:
    kind = str(value or "").strip().lower()
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
        """两步合成——返回 {one_liner, core_arguments, key_materials,
           knowledge_links, markdown}。"""
        if not captures:
            return {"error": "no captures provided"}

        # Step 1: 结构化提取
        content_text = "\n\n---\n\n".join(
            f"[{c.get('id','')[:8]}]《{c.get('title','')}》\n{c.get('cached_text','') or c.get('summary','')}"
            for c in captures
        )
        try:
            extracted = self._step1_extract(content_text, card_kind=card_kind)
        except Exception as exc:
            logger.warning("Garden structured extraction failed: %s", exc)
            return {"error": f"Garden 结构化提取失败：{exc.__class__.__name__}"}
        resolved_card_kind = (
            _normalize_card_kind(card_kind)
            if card_kind
            else _normalize_card_kind(extracted.get("card_kind"))
        )
        extracted["card_kind"] = resolved_card_kind

        # Step 2: 知识关联
        links = self._step2_link(extracted, existing_garden_cards)
        links = links + _infer_links_from_shared_sources(captures, existing_garden_cards)
        links = _normalize_knowledge_links(links, existing_garden_cards)

        # 合并生成 Markdown
        title = title or self._generate_title(extracted, captures)
        markdown = self._build_synthesis_markdown(
            title, extracted, links, captures, existing_garden_cards,
            resolved_card_kind,
        )

        return {
            "title": title,
            "card_kind": resolved_card_kind,
            "one_liner": extracted.get("one_liner", ""),
            "core_arguments": extracted.get("core_arguments", []),
            "key_materials": extracted.get("key_materials", []),
            "knowledge_links": links,
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
            f"- {a['claim']}" for a in extracted.get("core_arguments", [])[:5]
        )
        cards_text = "\n".join(
            f"- id={c['id']} title={c.get('title','')} one_liner={c.get('one_liner','')[:60]}"
            for c in existing_cards[:20]
        )
        prompt = LINK_PROMPT.format(
            one_liner=extracted.get("one_liner", ""),
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
        one_liner = extracted.get("one_liner", "")
        if model_title and model_title != one_liner:
            return model_title[:48]
        claims = extracted.get("core_arguments", [])
        for claim in claims:
            text = str(claim.get("claim", "")).strip()
            if text and text != one_liner:
                return text[:24]
        return one_liner[:20] if one_liner else (captures[0].get("title", "")[:20] or "未命名")

    def _build_synthesis_markdown(self, title: str, extracted: dict,
                                   links: list[str],
                                   captures: list[dict],
                                   existing_cards: list[dict],
                                   card_kind: str) -> str:
        card_kind = _normalize_card_kind(card_kind)
        h1, h2, h3, h4, h5 = CARD_KIND_HEADINGS[card_kind]
        one_liner = extracted.get("one_liner", "").strip() or "（待补充）"
        lines = [
            f"# {title}",
            "",
            f"## {h1}",
            one_liner,
            "",
            f"## {h2}",
        ]

        arguments = extracted.get("core_arguments", [])
        if arguments:
            for argument in arguments[:4]:
                claim = str(argument.get("claim", "")).strip()
                if not claim:
                    continue
                sources = argument.get("source_ids") or []
                source_text = f"（来源：{', '.join(sources)}）" if sources else ""
                lines.append(f"- {claim}{source_text}")
        else:
            lines.append("- （待补充）")

        lines.extend(["", f"## {h3}"])
        materials = extracted.get("key_materials", [])
        if materials:
            for material in materials[:4]:
                material_type = str(material.get("type", "material")).strip()
                content = str(material.get("content", "")).strip()
                capture_id = str(material.get("capture_id", "")).strip()
                if not content:
                    continue
                source_text = f"（{capture_id}）" if capture_id else ""
                lines.append(f"- [{material_type}] {content[:240]}{source_text}")
        else:
            lines.append("- （待补充）")

        lines.extend(["", f"## {h4}"])
        if h4 == "来源依据":
            self._append_source_notes(lines, captures)
        else:
            lines.append("- （待补充）")

        if h4 != "来源依据":
            lines.extend(["", "## 来源笔记"])
            self._append_source_notes(lines, captures)

        lines.extend(["", f"## {h5}"])
        if links:
            card_lookup = {str(card.get("id", "")): card for card in existing_cards}
            for link in links[:8]:
                lines.append(f"- {_format_knowledge_link(link, card_lookup)}")
        else:
            lines.append("- 暂无关联")

        lines.extend([
            "",
            "## 待继续想",
            "- 这张卡还需要结合你的判断继续修改、补边界或补反例。",
        ])
        return "\n".join(lines).strip() + "\n"

    def _append_source_notes(self, lines: list[str], captures: list[dict]) -> None:
        for capture in captures:
            cid = str(capture.get("id", "")).strip()
            capture_title = str(capture.get("title", "")).strip() or cid
            if cid:
                lines.append(f"- {capture_title}（{cid}）")
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


def create_garden_card(repo, garden_data: dict) -> str:
    """写入 garden_cards 表，返回 card ID。"""
    card_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    repo.conn.execute(
        """INSERT INTO garden_cards (id, title, one_liner, card_kind, core_arguments,
           knowledge_links, key_materials, my_view, status, capture_ids,
           created_at, updated_at, review_interval)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'seed', ?, ?, ?, '1d')""",
        (
            card_id,
            garden_data.get("title", ""),
            garden_data.get("one_liner", ""),
            garden_data.get("card_kind", "viewpoint"),
            json.dumps(garden_data.get("core_arguments", [])),
            json.dumps(garden_data.get("knowledge_links", [])),
            json.dumps(garden_data.get("key_materials", [])),
            "",
            json.dumps(garden_data.get("capture_ids", [])),
            now, now,
        ),
    )
    repo.conn.commit()
    return card_id


def _json_text(value) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def _json_list(text: str) -> list:
    try:
        value = json.loads(text or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


def create_garden_draft(repo, garden_data: dict) -> str:
    """写入 garden_drafts 表，返回 draft ID。"""
    draft_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    repo.conn.execute(
        """INSERT INTO garden_drafts (id, title, one_liner, card_kind, core_arguments,
           knowledge_links, key_materials, markdown, capture_ids, question_id,
           instruction, status, committed_card_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft_ready', '', ?, ?)""",
        (
            draft_id,
            garden_data.get("title", ""),
            garden_data.get("one_liner", ""),
            garden_data.get("card_kind", "viewpoint"),
            _json_text(garden_data.get("core_arguments", [])),
            _json_text(garden_data.get("knowledge_links", [])),
            _json_text(garden_data.get("key_materials", [])),
            garden_data.get("markdown", ""),
            _json_text(garden_data.get("capture_ids", [])),
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
    repo.conn.execute(
        """UPDATE garden_drafts
           SET title=?, one_liner=?, card_kind=?, core_arguments=?, knowledge_links=?,
               key_materials=?, markdown=?, instruction=?, status=?, updated_at=?
           WHERE id=?""",
        (
            garden_data.get("title", existing["title"]),
            garden_data.get("one_liner", existing["one_liner"]),
            garden_data.get("card_kind", existing["card_kind"]),
            _json_text(
                garden_data.get(
                    "core_arguments", _json_list(existing["core_arguments"])
                )
            ),
            _json_text(
                garden_data.get(
                    "knowledge_links", _json_list(existing["knowledge_links"])
                )
            ),
            _json_text(
                garden_data.get("key_materials", _json_list(existing["key_materials"]))
            ),
            garden_data.get("markdown", existing["markdown"]),
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
        "card_kind": draft["card_kind"],
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
    """更新已有 Garden Card。"""
    existing = repo.conn.execute(
        "SELECT * FROM garden_cards WHERE id = ?", (card_id,)
    ).fetchone()
    if not existing:
        return None

    now = datetime.now(timezone.utc).isoformat()
    # 合并参数（追加新论点, 去重）
    # 简化实现：覆盖更新
    repo.conn.execute(
        """UPDATE garden_cards SET title=?, one_liner=?, card_kind=?, core_arguments=?,
           knowledge_links=?, key_materials=?, updated_at=?, status='seed'
           WHERE id=?""",
        (
            garden_data.get("title", existing["title"]),
            garden_data.get("one_liner", existing["one_liner"]),
            garden_data.get("card_kind", existing["card_kind"]),
            json.dumps(garden_data.get("core_arguments", [])),
            json.dumps(garden_data.get("knowledge_links", [])),
            json.dumps(garden_data.get("key_materials", [])),
            now, card_id,
        ),
    )
    repo.conn.commit()
    return dict(existing)
