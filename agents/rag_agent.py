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


def build_rag_prompt(question: str, captures: list[dict]) -> str:
    parts = [f"用户问题：{question}\n\n知识库相关内容（用 [编号] 引用）：\n"]
    for i, c in enumerate(captures, 1):
        title = c.get("title") or "无标题"
        text = c.get("body") or c.get("story", "")
        parts.append(f"[{i}] 《{title}》\n{text[:8000]}\n")
    parts.append("请用 [1][2] 等编号在回答中标注每条信息的来源。")
    return "\n".join(parts)


def format_answer(answer: str, sources: list[dict]) -> str:
    # 答案截断（微信友好）
    if len(answer) > 600:
        answer = answer[:597] + "..."

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

    def query(self, question: str, captures: list[dict]) -> dict:
        if not captures:
            return {
                "answer": "知识库中没有找到相关信息。",
                "sources": [],
            }
        prompt = build_rag_prompt(question, captures)
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
