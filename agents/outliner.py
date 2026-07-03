"""LLM 文档大纲生成 — 仅长文(>2000字)触发."""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OUTLINE_SOURCE_CHAR_LIMIT = 6000
OUTLINE_MAX_TOKENS = 4000

OUTLINE_PROMPT = """分析以下按段落编号的文本，生成层级目录。

要求：
1. 每个章节包含：level (1|2|3), title (章节标题), start_paragraph_index (章节开始的段落编号，整数),
   summary (该节一句话摘要)
2. 不要超过 15 个章节
3. 不要展示思考过程、解释、前言或结尾
4. 只输出 JSON 数组，不要 markdown 代码块
5. start_paragraph_index 必须使用文本里已有的 [段N] 编号，且按章节顺序递增
6. 如果原文没有清晰标题，你需要为章节取一个简洁标题

文本：
{text}

直接输出 JSON 数组："""


class Outliner:
    def __init__(self, api_key: str, model: str = "deepseek-v4-pro",
                 base_url: str = "https://api.deepseek.com", retries: int = 3):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.retries = retries

    def generate(self, cleaned_text: str, word_count: int) -> list[dict]:
        if word_count <= 2000:
            return []

        prompt = OUTLINE_PROMPT.format(
            text=_numbered_paragraph_preview(cleaned_text, OUTLINE_SOURCE_CHAR_LIMIT)
        )

        for attempt in range(self.retries):
            try:
                return self._call(prompt)
            except Exception as e:
                logger.warning(
                    f"Outline attempt {attempt+1}/{self.retries} failed: {e}"
                )
                if attempt == self.retries - 1:
                    raise

        return []

    def _call(self, prompt: str) -> list[dict]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": OUTLINE_MAX_TOKENS,
            "thinking": {"type": "disabled"},
        }
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers=headers, json=body,
            )
            resp.raise_for_status()
            choice = resp.json()["choices"][0]
            raw = choice["message"].get("content") or ""
            if not raw.strip() and choice.get("finish_reason") == "length":
                raise ValueError("outline response was empty after hitting token limit")
            return _parse_outline_json(raw)


def _parse_outline_json(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith("```"):
        text = text[text.index("\n"):]
        if text.endswith("```"):
            text = text[:-3]
    text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])

    if not isinstance(parsed, list):
        raise ValueError("outline response must be a JSON array")
    return parsed


def _numbered_paragraph_preview(text: str, limit: int) -> str:
    blocks: list[str] = []
    for index, paragraph in enumerate(_outline_paragraphs(text), 1):
        line = paragraph if len(paragraph) <= 700 else paragraph[:700].rstrip() + "..."
        blocks.append(f"[段{index}] {line}")
        preview = "\n\n".join(blocks)
        if len(preview) >= limit:
            return preview[:limit].rstrip()
    return "\n\n".join(blocks)[:limit].rstrip()


def _outline_paragraphs(text: str) -> list[str]:
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", text or ""):
        paragraph = re.sub(r"\s+", " ", block).strip()
        if not paragraph:
            continue
        if re.match(r"^#{1,6}\s+", paragraph):
            continue
        if paragraph.startswith("目录纲要"):
            continue
        paragraphs.append(paragraph)
    return paragraphs
