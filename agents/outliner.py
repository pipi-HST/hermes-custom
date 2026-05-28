"""LLM 文档大纲生成 — 仅长文(>2000字)触发."""
from __future__ import annotations

import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OUTLINE_PROMPT = """分析以下文本，生成层级目录。

要求：
1. 每个章节包含：level (1|2|3), title (章节标题), range (如"段3-7"),
   summary (该节一句话摘要)
2. 不要超过 15 个章节
3. 只输出 JSON 数组，不要 markdown 代码块

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

        prompt = OUTLINE_PROMPT.format(text=cleaned_text[:12000])

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
            "max_tokens": 2000,
        }
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers=headers, json=body,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw[raw.index("\n"):]
                if raw.endswith("```"):
                    raw = raw[:-3]
            return json.loads(raw.strip())
