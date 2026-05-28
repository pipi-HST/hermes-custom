"""LLM-based summarizer with tag generation."""
from __future__ import annotations

import json
import re
from typing import Optional

import httpx

SEED_TAGS = [
    "ai-agent", "ai-rag", "ai-product", "product-design",
    "health-sleep", "health-exercise", "health-mood",
    "personal-decision", "career", "finance", "reading-notes",
]

SUMMARY_PROMPT = """你是一个内容整理助手。根据以下清洗后的文本，生成：

1. 文章标题（10 字以内，提取或归纳核心主题）
2. 一句中文摘要（不超过 50 字）
3. 3-5 个标签（格式：领域前缀-关键词，如 ai-agent）
⚠️ 重要规则：
- 如果以下文本明显不是完整的文章正文，直接输出 summary: "提取内容不完整"。
- 不要根据标题推测内容——只根据实际提供的文本生成摘要。
- 不要编造文本中没有提到的内容。
已有高频标签参考（鼓励复用，也可创造新标签）：
{existing_tags}

清洗后的文本：
{cleaned_text}

请严格按照以下格式回复：
---
title: <文章标题>
summary: <一句话中文摘要>
tags: ["tag1", "tag2", "tag3"]
---"""


class Summarizer:
    def __init__(self, api_key: str, provider: str = "openai",
                 model: str = "gpt-4o", base_url: Optional[str] = None):
        self.api_key = api_key
        self.provider = provider
        self.model = model
        self.base_url = base_url or "https://api.deepseek.com"
        self.seed_tags = SEED_TAGS

    def summarize(self, cleaned_text: str,
                  existing_tags: Optional[list[str]] = None) -> dict:
        if not cleaned_text.strip():
            return {"title": "", "summary": "", "tags": []}

        tags = existing_tags or self.seed_tags
        prompt = self._build_summary_prompt(cleaned_text, tags)
        response = self._call_llm(prompt)
        return self._parse_response(response)

    def _build_summary_prompt(self, cleaned_text: str,
                              existing_tags: list[str]) -> str:
        tags_str = ", ".join(existing_tags[:30])
        return SUMMARY_PROMPT.format(
            existing_tags=tags_str,
            cleaned_text=cleaned_text[:8000],
        )

    def _call_llm(self, prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是一个专业的内容整理助手。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 4000,
        }
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers=headers, json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    def _parse_response(self, raw: str) -> dict:
        title_match = re.search(r"title:\s*(.+?)(?:\n|$)", raw)
        summary_match = re.search(r"summary:\s*(.+?)(?:\n|$)", raw)
        tags_match = re.search(r"tags:\s*\[(.+?)\]", raw)

        title = title_match.group(1).strip() if title_match else ""
        summary = summary_match.group(1).strip() if summary_match else ""
        tags: list[str] = []
        if tags_match:
            try:
                tags = json.loads(f"[{tags_match.group(1)}]")
            except json.JSONDecodeError:
                tags = [t.strip().strip('"') for t in tags_match.group(1).split(",")]

        if not summary:
            lines = [l.strip() for l in raw.split("\n") if l.strip()]
            summary = lines[0][:100] if lines else "无摘要"

        return {"title": title, "summary": summary, "tags": tags[:5]}
