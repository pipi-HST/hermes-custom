"""LLM-based summarizer with tag generation."""
from __future__ import annotations

import json
import re
from typing import Optional

import httpx

from bridge.ai_cancel import raise_if_cancelled

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
1. 生成标题、摘要、标签、结构化 Markdown 正文、文件夹建议和 Garden Card 建议。
2. body_markdown 只改变可读性：分段、空行、二/三级标题、列表、加粗原文关键词、轻微错别字或标点修正。
3. 不得总结式改写正文，不得删减信息、不得改变原文观点、不得打乱原文顺序、不得丢失例子和信息密度，不做压缩总结。
4. 不得把多段原文合并成概述；原文中的段落、例子、枚举、引用和操作步骤应逐段保留，只允许调整排版和标题层级。
5. body_markdown 长度不得显著短于原文；除非原文中存在重复目录、重复标题或图片占位污染，整理后至少保留原文约 80% 的信息量和正文篇幅。
6. 长文不得把多个主题塞进一个大段落；每个主题、经验、步骤、案例或观点应拆成独立段落或章节。
7. 如果生成了“## 目录纲要”，目录纲要中的每个主要条目都必须在正文后续位置有同名或近似同名的 Markdown heading，方便网页端跳转。
8. 不得输出空标题，不得输出只有 ### 或 ## 的空 Markdown 标题；标题后必须有具体标题文本。
9. 不得把目录纲要条目复制或混入正文段落；目录纲要只放在“## 目录纲要”区域，正文中要使用真实 Markdown heading 承接目录条目。
10. 裸图片文件名或占位符不得作为标题或目录项；遇到 image.png 这类占位时，保留为“图片占位：image.png”。
11. 如果不确定如何整理正文，body_markdown 应返回仅做空白行、段落和行尾清理后的原文。
12. 摘要必须基于正文实际内容，不要编造事实、来源、作者、日期。
13. 文件夹建议必须基于下面列出的“已有文件夹”。只能从“已有文件夹”中选择 existing；如果没有合适分类，才建议 new。
14. 未列出的分类不能当作 existing，只能作为 new 建议。
15. 需要判断是否适合生成 Garden Card，并给出非空 reason。
16. 如果适合生成 Garden Card，需要建议生成哪些卡片，供后续 Garden Card Agent 决策。
17. 不要输出 Markdown 代码块包裹 JSON；JSON 内的 body_markdown 字符串可以包含 Markdown。

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
  "body_markdown": "",
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
        max_tokens = self._editable_draft_max_tokens(content)
        response = self._call_llm(prompt, max_tokens=max_tokens, json_mode=True)
        parsed = self._parse_json_response(response)
        if not parsed:
            return self._editable_draft_safe_defaults(
                content,
                warning="AI 整理结果解析失败，请手动填写摘要、标签和建议。",
                diagnostics=self._editable_parse_diagnostics(
                    response,
                    prompt_len=len(prompt),
                    max_tokens=max_tokens,
                ),
            )
        return self._normalize_editable_draft(parsed, content)

    def _build_summary_prompt(self, cleaned_text: str,
                              existing_tags: list[str]) -> str:
        tags_str = ", ".join(existing_tags[:30])
        return SUMMARY_PROMPT.format(
            existing_tags=tags_str,
            cleaned_text=cleaned_text,
        )

    def _editable_draft_max_tokens(self, content: str) -> int:
        return min(24000, max(4000, len(content) * 3))

    def _editable_parse_diagnostics(
        self,
        raw: str,
        *,
        prompt_len: int,
        max_tokens: int,
    ) -> dict:
        response = raw or ""
        return {
            "parse_error_kind": "json_parse_failed",
            "response_len": len(response),
            "response_head": response[:500],
            "response_tail": response[-500:],
            "max_tokens": max_tokens,
            "prompt_len": prompt_len,
        }

    def _call_llm(
        self,
        prompt: str,
        *,
        max_tokens: int = 4000,
        json_mode: bool = False,
    ) -> str:
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
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        raise_if_cancelled("summarizer.before_llm")
        with httpx.Client(timeout=300) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers=headers, json=body,
            )
            if json_mode and resp.status_code in {400, 422}:
                body.pop("response_format", None)
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers, json=body,
                )
            resp.raise_for_status()
            data = resp.json()
            raise_if_cancelled("summarizer.after_llm")
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
        folder_suggestion = (
            data.get("folder_suggestion")
            if isinstance(data.get("folder_suggestion"), dict)
            else {}
        )
        card_suggestion = (
            data.get("card_suggestion")
            if isinstance(data.get("card_suggestion"), dict)
            else {}
        )
        return {
            "title": str(data.get("title") or "").strip(),
            "summary": str(data.get("summary") or "").strip(),
            "tags": [str(tag).strip() for tag in tags if str(tag).strip()][:8],
            "body_markdown": str(data.get("body_markdown") or "").strip(),
            "folder_suggestion": self._normalize_folder_suggestion(folder_suggestion),
            "card_suggestion": self._normalize_card_suggestion(card_suggestion),
            "warnings": [str(item).strip() for item in warnings if str(item).strip()],
        }

    def _editable_draft_safe_defaults(
        self,
        content: str,
        *,
        warning: str,
        diagnostics: dict | None = None,
    ) -> dict:
        return {
            "title": "",
            "summary": "",
            "tags": [],
            "body_markdown": "",
            "folder_suggestion": self._default_folder_suggestion(),
            "card_suggestion": self._default_card_suggestion("AI 未返回有效建议。"),
            "warnings": [warning],
            "diagnostics": diagnostics or {},
        }

    def _normalize_folder_suggestion(self, suggestion: dict) -> dict:
        mode = str(suggestion.get("mode") or "").strip()
        folder_path = str(suggestion.get("folder_path") or "").strip()
        reason = str(suggestion.get("reason") or "").strip()
        if mode not in {"existing", "new"}:
            mode = "existing" if not folder_path or folder_path == "Inbox" else "new"
        if not folder_path:
            folder_path = "Inbox"
            mode = "existing"
        if not reason:
            reason = "AI 未返回明确文件夹建议，先放入 Inbox 等待人工确认。"
        return {"mode": mode, "folder_path": folder_path, "reason": reason}

    def _normalize_card_suggestion(self, suggestion: dict) -> dict:
        should_generate = bool(suggestion.get("should_generate"))
        reason = str(suggestion.get("reason") or "").strip()
        cards = suggestion.get("cards") if isinstance(suggestion.get("cards"), list) else []
        if not reason:
            reason = "AI 未返回有效卡片建议，默认暂不生成 Garden Card。"
            should_generate = False
        return {
            "should_generate": should_generate,
            "reason": reason,
            "cards": cards,
        }

    def _default_folder_suggestion(self) -> dict:
        return {
            "mode": "existing",
            "folder_path": "Inbox",
            "reason": "AI 整理结果解析失败，先放入 Inbox 等待人工确认。",
        }

    def _default_card_suggestion(self, reason: str) -> dict:
        return {
            "should_generate": False,
            "reason": reason,
            "cards": [],
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
            lines = [line.strip() for line in raw.split("\n") if line.strip()]
            summary = lines[0][:100] if lines else "无摘要"

        return {"title": title, "summary": summary, "tags": tags[:5]}
