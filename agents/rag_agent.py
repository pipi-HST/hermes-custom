"""RAG 问答：检索→LLM 生成→来源引用."""
from __future__ import annotations

import re
import httpx

RAG_SYSTEM_PROMPT = """你是个人知识助手。根据用户的知识库内容回答问题。

规则：
1. 用中文回答，简洁直接
2. 每个关键论点必须用 [1][2] 等编号标注来源文档
3. 如果内容中有矛盾观点，同时呈现并标出差异
4. 如果库中没有相关信息，诚实说不知道
5. 不要编造知识库中没有的内容"""


def build_rag_prompt(
    question: str,
    captures: list[dict],
    *,
    context_pack: dict | None = None,
) -> str:
    parts = [f"用户问题：{question}\n"]
    orientation = _format_orientation_context(context_pack or {})
    if orientation:
        parts.append(
            "\n高层线索（帮助理解材料之间的关系；不要把高层线索当作编号来源）：\n"
        )
        parts.append(orientation)
    parts.append("\n知识库相关内容（只用这些 [编号] 作为答案来源）：\n")
    for i, c in enumerate(captures, 1):
        title = c.get("title") or "无标题"
        text = c.get("body") or c.get("story", "")
        parts.append(f"[{i}] 《{title}》\n{text[:8000]}\n")
    parts.append("请用 [1][2] 等编号在回答中标注每条信息的来源。")
    return "\n".join(parts)


def format_answer(answer: str, sources: list[dict]) -> str:
    # 答案截断（微信友好）
    answer = _clean_partial_citation_suffix(answer or "")
    if len(answer) > 600:
        answer = _truncate_answer(answer, 600)

    lines = [answer, "", "📎 来源"]
    cited_indexes = _cited_source_indexes(answer, len(sources))
    if cited_indexes:
        selected = [(idx, sources[idx - 1]) for idx in cited_indexes]
    else:
        selected = [(idx, source) for idx, source in enumerate(sources[:3], 1)]

    for i, s in selected:
        title = s.get("title") or "无标题"
        story = s.get("story", "") or s.get("summary", "")
        story = story[:30] + "..." if len(story) > 30 else story
        line = f"[{i}] {title}"
        if story:
            line += f" — {story}"
        lines.append(line)

    result = "\n".join(lines)
    # 总长度兜底
    if len(result) > 900:
        result = result[:897] + "..."
    return result


def _truncate_answer(answer: str, limit: int) -> str:
    cutoff = _clean_partial_citation_suffix(answer[:max(0, limit - 3)]).rstrip()
    return cutoff + "..."


def _clean_partial_citation_suffix(answer: str) -> str:
    return re.sub(r"\[(?:\d{0,3})?$", "", answer).rstrip()


def _cited_source_indexes(answer: str, source_count: int) -> list[int]:
    indexes: list[int] = []
    for match in re.finditer(r"\[(\d{1,3})\]", answer or ""):
        idx = int(match.group(1))
        if 1 <= idx <= source_count and idx not in indexes:
            indexes.append(idx)
    return indexes


class RagAgent:
    def __init__(self, llm_api_key: str,
                 llm_model: str = "deepseek-v4-pro",
                 llm_base_url: str = "https://api.deepseek.com"):
        self.llm_api_key = llm_api_key
        self.llm_model = llm_model
        self.llm_base_url = llm_base_url

    def query(
        self,
        question: str,
        captures: list[dict],
        *,
        context_pack: dict | None = None,
    ) -> dict:
        if not captures:
            return {
                "answer": "知识库中没有找到相关信息。",
                "sources": [],
            }
        prompt = build_rag_prompt(question, captures, context_pack=context_pack)
        answer = self._call_llm(prompt)
        sources = [{
            "title": c.get("title", ""),
            "summary": c.get("story", ""),
            "file_path": c.get("file_path", ""),
            "section_title": c.get("section_title", ""),
        } for c in captures]
        return {"answer": answer, "sources": sources}

    def _call_llm(self, prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json",
        }
        is_simple = len(prompt) < 3000
        body = {
            "model": self.llm_model if not is_simple else "deepseek-v4-flash",
            "messages": [
                {"role": "system", "content": RAG_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 800,
        }
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{self.llm_base_url}/chat/completions",
                headers=headers, json=body,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]


def _format_orientation_context(context_pack: dict) -> str:
    lines: list[str] = []
    garden_cards = context_pack.get("garden_cards") or []
    if garden_cards:
        lines.append("Garden 卡片：")
        for card in garden_cards[:3]:
            title = card.get("title") or "未命名"
            one_liner = card.get("one_liner") or ""
            lines.append(f"- {title}: {one_liner}")
    theme_pages = context_pack.get("theme_pages") or []
    if theme_pages:
        lines.append("Theme 页面：")
        for theme in theme_pages[:3]:
            title = theme.get("title") or theme.get("path") or "未命名"
            lines.append(f"- {title}")
    theme_groups = context_pack.get("theme_groups") or []
    if theme_groups:
        lines.append("Theme 候选组：")
        for group in theme_groups[:3]:
            title = group.get("title") or "未命名"
            lines.append(f"- {title}")
    graph_trace = context_pack.get("graph_trace") or {}
    if graph_trace:
        lines.append(
            "检索路径："
            f"seed={graph_trace.get('seed_count', 0)}, "
            f"garden={graph_trace.get('garden_count', 0)}, "
            f"theme={graph_trace.get('theme_count', 0)}, "
            f"final={graph_trace.get('final_count', 0)}"
        )
    if garden_cards or theme_pages or theme_groups or graph_trace:
        lines.append("Garden/Theme 是理解线索；编号来源只来自下方 Capture 证据。")
    return "\n".join(lines)
