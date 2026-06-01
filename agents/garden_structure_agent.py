"""LLM planner for structured Garden Canvas clusters."""
from __future__ import annotations

import json
from typing import Any

import httpx

from agents.garden_agent import _parse_json_object

STRUCTURE_PROMPT = """你是 Hermes 的 Garden Structure Agent。

你的任务不是总结材料，也不是生成 MOC，而是判断一组 Garden Cards 是否已经形成一个值得画成 Obsidian Canvas 的知识结构。

主题：{theme}

Garden Cards:
{cards_text}

请只返回 JSON，不要 markdown 代码块，不要解释。

如果这些卡只是同主题堆在一起，没有清晰关系，返回：
{{
  "status": "not_worth_canvas",
  "reason": "为什么 MOC 足够，不值得生成 Canvas"
}}

如果存在清晰结构，返回：
{{
  "status": "structured",
  "core_claim": "这些卡合在一起说明了什么，一句话",
  "groups": [
    {{
      "role": "support|method|risk|question|fact",
      "title": "这一组承担的知识角色",
      "card_ids": ["必须来自输入里的完整 card id"],
      "reason": "为什么这些卡应该放在这一组"
    }}
  ],
  "evidence_ids": ["可选，相关来源 capture id"]
}}

要求：
- 让 card 承担知识角色：支撑、方法、风险、问题、事实等。
- 不要为了凑图硬分组；没有结构就返回 not_worth_canvas。
- card_ids 必须使用输入里的完整 id。
- groups 保留 2-5 组，每组至少 1 张卡。
- core_claim 必须是结构判断，不要只是主题名。"""

ROLE_ALIASES = {
    "support": "support",
    "supporting": "support",
    "supporting_claims": "support",
    "claim": "support",
    "claims": "support",
    "viewpoint": "support",
    "method": "method",
    "practice": "method",
    "how_to": "method",
    "how-to": "method",
    "risk": "risk",
    "boundary": "risk",
    "caution": "risk",
    "question": "question",
    "open_question": "question",
    "open-question": "question",
    "fact": "fact",
    "evidence": "fact",
}


class GardenStructureAgent:
    def __init__(
        self,
        llm_api_key: str,
        llm_model: str = "deepseek-v4-pro",
        llm_base_url: str = "https://api.deepseek.com",
    ):
        self.llm_api_key = llm_api_key
        self.llm_model = llm_model
        self.llm_base_url = llm_base_url

    def plan(self, theme: str, garden_cards: list[dict[str, Any]]) -> dict[str, Any]:
        if not garden_cards:
            return {
                "status": "not_worth_canvas",
                "reason": "没有可用于结构化的 Garden Card。",
            }

        prompt = STRUCTURE_PROMPT.format(
            theme=theme or "未命名主题",
            cards_text=_cards_text(garden_cards),
        )
        try:
            raw = self._call_llm(prompt, max_tokens=2000)
            payload = _parse_json_object(raw)
        except Exception as exc:
            return {
                "status": "not_worth_canvas",
                "reason": f"结构规划失败：{exc.__class__.__name__}",
            }
        return _sanitize_plan(payload, garden_cards)

    def _call_llm(self, prompt: str, max_tokens: int = 2000) -> str:
        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{self.llm_base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]


def _sanitize_plan(payload: dict[str, Any], garden_cards: list[dict[str, Any]]) -> dict[str, Any]:
    if payload.get("status") != "structured":
        return {
            "status": "not_worth_canvas",
            "reason": str(payload.get("reason") or "这些卡没有形成清晰结构，MOC 已经足够。"),
        }

    core_claim = str(payload.get("core_claim") or "").strip()
    if not core_claim:
        return {
            "status": "not_worth_canvas",
            "reason": "没有形成可说明的核心结构判断。",
        }

    valid_card_ids = {
        str(card.get("id") or "").strip()
        for card in garden_cards
        if str(card.get("id") or "").strip()
    }
    groups = []
    seen_cards: set[str] = set()
    for raw_group in payload.get("groups") or []:
        if not isinstance(raw_group, dict):
            continue
        card_ids = _dedupe([
            str(card_id).strip()
            for card_id in raw_group.get("card_ids") or []
            if str(card_id).strip() in valid_card_ids
        ])
        card_ids = [card_id for card_id in card_ids if card_id not in seen_cards]
        if not card_ids:
            continue
        seen_cards.update(card_ids)
        groups.append({
            "role": _normalize_role(str(raw_group.get("role") or "")),
            "title": str(raw_group.get("title") or "").strip() or "相关卡片",
            "card_ids": card_ids,
            "reason": str(raw_group.get("reason") or "").strip(),
        })

    if not groups:
        return {
            "status": "not_worth_canvas",
            "reason": "结构规划结果没有包含有效 Garden Card。",
        }

    return {
        "status": "structured",
        "core_claim": core_claim,
        "groups": groups,
        "evidence_ids": _dedupe([
            str(item).strip()
            for item in payload.get("evidence_ids") or []
            if str(item).strip()
        ]),
    }


def _cards_text(garden_cards: list[dict[str, Any]]) -> str:
    lines = []
    for card in garden_cards[:20]:
        card_id = str(card.get("id") or "").strip()
        title = str(card.get("title") or "").strip()
        one_liner = str(card.get("one_liner") or "").strip()
        kind = str(card.get("card_kind") or "").strip()
        source_ids = _json_list(card.get("capture_ids"))
        lines.append(
            "- id={id} kind={kind} title={title} one_liner={one_liner} sources={sources}".format(
                id=card_id,
                kind=kind or "unknown",
                title=title or "未命名",
                one_liner=one_liner or "（无）",
                sources=",".join(source_ids[:5]) or "none",
            )
        )
    return "\n".join(lines)


def _normalize_role(role: str) -> str:
    key = role.strip().lower()
    return ROLE_ALIASES.get(key, "support")


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(value or "[]") if isinstance(value, str) else value
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _dedupe(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
