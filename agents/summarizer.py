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

SUMMARY_PROMPT = """你是一个内容整理助手。请通读以下完整文本，基于全文生成元数据：

1. 文章标题（提取或归纳核心主题，尽量简洁）
2. 一句中文摘要（不超过 80 字，必须覆盖全文核心结论，不要只复述开头）
3. 3-5 个标签（格式：领域前缀-关键词，如 ai-agent）
⚠️ 重要规则：
- 如果以下文本明显不是完整的文章正文，直接输出 summary: "提取内容不完整"。
- 不要根据标题推测内容——只根据实际提供的文本生成摘要。
- 不要编造文本中没有提到的内容。
- 不要把正文原句机械截取为摘要；摘要要综合全文后归纳。
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

EDITABLE_DRAFT_PROMPT = """你是 Hermes Knowledge 的知识整理助手。你的任务是把用户提供的内容整理成可编辑、可入库的知识草稿。

严格要求：
1. 只生成标题、摘要、标签、文件夹建议和 Garden Card 建议。
2. 不要总结式改写正文，不要改变原文观点、顺序、例子和信息密度。
3. 摘要必须基于正文实际内容，不要编造事实、来源、作者、日期。
4. 需要判断内容适合放入已有文件夹，还是建议新建文件夹。
5. 需要判断是否适合生成 Garden Card。
6. 如果适合生成 Garden Card，需要建议生成哪些卡片，供后续 Garden Card Agent 决策。
7. 不要返回正文，不要返回 body_markdown 字段。正文由用户直接编辑和确认，你只负责生成元信息。

已有文件夹：
{existing_folders}

已有高频标签参考：
{existing_tags}

用户原始内容：
{content}

请只输出 JSON，不要输出 Markdown 代码块：
{{
  "title": "",
  "summary": "",
  "tags": [],
  "folder_suggestion": {{
    "mode": "existing 或 new",
    "folder_path": "",
    "reason": ""
  }},
  "card_suggestion": {{
    "should_generate": true,
    "reason": "",
    "cards": [
      {{
        "title": "",
        "card_kind": "viewpoint 或 method 或 fact 或 risk",
        "reason": ""
      }}
    ]
  }},
  "warnings": []
}}"""

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
        response = self._call_llm(prompt, max_tokens=4000)
        return self._parse_response(response)

    def organize_editable_draft(
        self,
        content: str,
        *,
        existing_tags: Optional[list[str]] = None,
        existing_folders: Optional[list[str]] = None,
    ) -> dict:
        if not content.strip():
            return {
                "title": "",
                "summary": "",
                "tags": [],
                "body_markdown": "",
                "folder_suggestion": {},
                "card_suggestion": {},
                "warnings": [],
            }
        prompt = EDITABLE_DRAFT_PROMPT.format(
            existing_folders=", ".join((existing_folders or [])[:30]) or "Inbox",
            existing_tags=", ".join((existing_tags or self.seed_tags)[:30]),
            content=content,
        )
        response = self._call_llm(prompt, max_tokens=4000)
        parsed = self._parse_json_response(response)
        if not parsed:
            return self._editable_draft_safe_defaults(
                content,
                warning="AI 整理结果解析失败，请手动填写摘要、标签和建议。",
            )
        return self._normalize_editable_draft(parsed, content)

    def _build_summary_prompt(self, cleaned_text: str,
                              existing_tags: list[str]) -> str:
        tags_str = ", ".join(existing_tags[:30])
        return SUMMARY_PROMPT.format(
            existing_tags=tags_str,
            cleaned_text=cleaned_text,
        )

    def _call_llm(self, prompt: str, *, max_tokens: int = 4000) -> str:
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
            "max_tokens": max_tokens,
        }
        with httpx.Client(timeout=300) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers=headers, json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    def _parse_json_response(self, raw: str) -> dict:
        text = (raw or "").strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        candidates = [text]
        balanced = self._extract_balanced_json_object(text)
        if balanced and balanced != text:
            candidates.append(balanced)
        for candidate in candidates:
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            return data if isinstance(data, dict) else {}
        return {}

    def _extract_balanced_json_object(self, text: str) -> str:
        start = text.find("{")
        if start < 0:
            return ""
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\" and in_string:
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]
        return ""

    def _normalize_editable_draft(self, data: dict, content: str) -> dict:
        tags = data.get("tags") if isinstance(data.get("tags"), list) else []
        warnings = data.get("warnings") if isinstance(data.get("warnings"), list) else []
        return {
            "title": str(data.get("title") or "").strip(),
            "summary": str(data.get("summary") or "").strip(),
            "tags": [str(tag).strip() for tag in tags if str(tag).strip()][:8],
            "body_markdown": "",
            "folder_suggestion": (
                data.get("folder_suggestion")
                if isinstance(data.get("folder_suggestion"), dict)
                else {}
            ),
            "card_suggestion": (
                data.get("card_suggestion")
                if isinstance(data.get("card_suggestion"), dict)
                else {}
            ),
            "warnings": [str(item).strip() for item in warnings if str(item).strip()],
        }

    def _editable_draft_safe_defaults(self, content: str, *, warning: str) -> dict:
        return {
            "title": "",
            "summary": "",
            "tags": [],
            "body_markdown": "",
            "folder_suggestion": {},
            "card_suggestion": {
                "should_generate": False,
                "reason": "AI 未返回有效建议。",
            },
            "warnings": [warning],
        }

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
