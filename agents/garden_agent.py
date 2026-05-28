"""Garden Agent — 两步合成 + 知识关联 + 生命周期管理."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = """分析以下知识库内容，提取结构化信息。

内容：
{content_text}

输出 JSON（不要 markdown 代码块）：
{{
  "one_liner": "一句话核心结论（30字以内）",
  "core_arguments": [
    {{"claim": "论点", "consensus": true|false, "source_ids": ["capture_id"]}}
  ],
  "key_materials": [
    {{"type": "quote|data|case", "content": "原文段落", "capture_id": "xxx"}}
  ]
}}"""

LINK_PROMPT = """为以下新 Garden Card 寻找知识关联。

新 Card:
- 一句话: {one_liner}
- 论点: {arguments_text}

已有 Garden Cards:
{existing_cards}

判断与哪些已有 Card 有知识关联。输出 JSON 数组：
["garden_card_id_1", "garden_card_id_2"]

只输出 JSON，不要其他内容。没有关联输出 []。"""

SYNTHESIS_PROMPT = """将以下结构化信息合并为一张知识卡片（Markdown 格式）。

结构化信息:
- 一句话: {one_liner}
- 论点: {arguments_text}
- 素材: {materials_text}
- 知识关联: {links_text}

输出 Markdown：

# {title}

## 一句话
{one_liner}

## 核心论点
- 共识论点1（来源：A, B）
- ...

## 知识关联
- 关联到：...

## 关键素材
- 引用：...
- 数据：...
- 案例：...

## 我的看法
（留空，由用户填写）
"""


class GardenAgent:
    def __init__(self, llm_api_key: str, llm_model: str = "deepseek-v4-pro",
                 llm_base_url: str = "https://api.deepseek.com"):
        self.llm_api_key = llm_api_key
        self.llm_model = llm_model
        self.llm_base_url = llm_base_url

    def synthesize(self, captures: list[dict],
                   existing_garden_cards: list[dict],
                   title: str = "") -> dict:
        """两步合成——返回 {one_liner, core_arguments, key_materials,
           knowledge_links, markdown}。"""
        if not captures:
            return {"error": "no captures provided"}

        # Step 1: 结构化提取
        content_text = "\n\n---\n\n".join(
            f"[{c.get('id','')[:8]}]《{c.get('title','')}》\n{c.get('cached_text','') or c.get('summary','')}"
            for c in captures
        )
        extracted = self._step1_extract(content_text)

        # Step 2: 知识关联
        links = self._step2_link(extracted, existing_garden_cards)

        # 合并生成 Markdown
        title = title or self._generate_title(extracted, captures)
        markdown = self._build_synthesis_markdown(
            title, extracted, links, captures,
        )

        return {
            "title": title,
            "one_liner": extracted.get("one_liner", ""),
            "core_arguments": extracted.get("core_arguments", []),
            "key_materials": extracted.get("key_materials", []),
            "knowledge_links": links,
            "markdown": markdown,
        }

    def _step1_extract(self, content_text: str) -> dict:
        prompt = EXTRACT_PROMPT.format(content_text=content_text[:12000])
        raw = self._call_llm(prompt, max_tokens=2000)
        return json.loads(raw.strip().lstrip("```json").rstrip("```"))

    def _step2_link(self, extracted: dict,
                     existing_cards: list[dict]) -> list[str]:
        if not existing_cards:
            return []
        args_text = "\n".join(
            f"- {a['claim']}" for a in extracted.get("core_arguments", [])[:5]
        )
        cards_text = "\n".join(
            f"- id={c['id'][:8]} one_liner={c.get('one_liner','')[:60]}"
            for c in existing_cards[:20]
        )
        prompt = LINK_PROMPT.format(
            one_liner=extracted.get("one_liner", ""),
            arguments_text=args_text,
            existing_cards=cards_text,
        )
        try:
            raw = self._call_llm(prompt, max_tokens=200)
            return json.loads(raw.strip().lstrip("```json").rstrip("```"))
        except (json.JSONDecodeError, Exception):
            return []

    def _generate_title(self, extracted: dict, captures: list[dict]) -> str:
        one_liner = extracted.get("one_liner", "")
        return one_liner[:20] if one_liner else (captures[0].get("title", "")[:20] or "未命名")

    def _build_synthesis_markdown(self, title: str, extracted: dict,
                                   links: list[str],
                                   captures: list[dict]) -> str:
        args_text = "\n".join(
            f"- {'[共识]' if a.get('consensus') else '[观点]'} {a['claim']}"
            for a in extracted.get("core_arguments", [])
        )
        materials_text = "\n".join(
            f"- [{m['type']}] {m['content'][:200]}"
            for m in extracted.get("key_materials", [])[:5]
        )
        links_text = ", ".join(links[:5]) if links else "暂无关联"
        return SYNTHESIS_PROMPT.format(
            title=title, one_liner=extracted.get("one_liner", ""),
            arguments_text=args_text, materials_text=materials_text,
            links_text=links_text,
        )

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


def create_garden_card(repo, garden_data: dict) -> str:
    """写入 garden_cards 表，返回 card ID。"""
    card_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    repo.conn.execute(
        """INSERT INTO garden_cards (id, title, one_liner, core_arguments,
           knowledge_links, key_materials, my_view, status, capture_ids,
           created_at, updated_at, review_interval)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'seed', ?, ?, ?, '1d')""",
        (
            card_id,
            garden_data.get("title", ""),
            garden_data.get("one_liner", ""),
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
        """UPDATE garden_cards SET title=?, one_liner=?, core_arguments=?,
           knowledge_links=?, key_materials=?, updated_at=?, status='seed'
           WHERE id=?""",
        (
            garden_data.get("title", existing["title"]),
            garden_data.get("one_liner", existing["one_liner"]),
            json.dumps(garden_data.get("core_arguments", [])),
            json.dumps(garden_data.get("knowledge_links", [])),
            json.dumps(garden_data.get("key_materials", [])),
            now, card_id,
        ),
    )
    repo.conn.commit()
    return dict(existing)
