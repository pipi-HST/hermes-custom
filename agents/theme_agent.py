"""Theme Agent — 事件驱动聚类发现 + 渐进式三级过滤 + 提问生成."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

THEME_PROMPT = """判断新内容和以下候选内容之间的关联类型。

新内容摘要：{new_summary}
新内容标签：{new_tags}

候选列表：
{candidates_text}

对每条候选，标注关联类型和理由。
只输出 JSON 数组，不要其他内容：
[{{"capture_id": "...", "related": true|false, "connection_type": "strong"|"weak"|"duplicate", "reason": "..."}}]"""

THEME_NAME_PROMPT = """根据以下笔记的标题和摘要，生成一个简洁的中文主题名。

要求：
1. 4-10 个字，自然语言风格（不要用 slug 格式）
2. 概括这些笔记的共同主题
3. 不要加引号

笔记：
{captures_text}

直接输出主题名："""

MOC_CONTENT_PROMPT = """根据主题名和相关笔记，生成主题页内容。

主题名：{theme_name}

相关笔记：
{captures_text}

输出 JSON（不要其他内容）：
{{"description": "2-3 句话描述这个主题的核心问题和探讨方向", "open_questions": ["2-3 个值得思考的开放性问题"]}}"""

QUESTION_PROMPT = """根据以下聚类信息，生成一条自然的中文提问。

你的任务是用友好的语气告诉用户：TA 最近存的几篇文章之间有关联，
并简要说明这些内容在讲什么，问用户要不要帮你整理成知识卡片。

聚类主题：{theme}
关联条目：
{items_text}

要求：
1. 2-3 句话，简洁自然
2. 提到每篇内容的要点差异或互补之处
3. 结尾问"要我帮你整理成一张知识卡片吗？"
4. 用"你"称呼用户

直接输出提问文本："""


class ThemeAgent:
    def __init__(self, llm_api_key: str, llm_model: str = "deepseek-v4-pro",
                 llm_base_url: str = "https://api.deepseek.com"):
        self.llm_api_key = llm_api_key
        self.llm_model = llm_model
        self.llm_base_url = llm_base_url

    def scan(self, new_captures: list[dict], all_captures: list[dict],
             top_k: int = 15) -> list[dict]:
        """对一批新 capture 执行渐进式三级过滤，返回强关联聚类。"""
        from agents.embedder import cosine_similarity

        clusters: list[dict] = []

        for new_cap in new_captures:
            new_emb = new_cap.get("embedding")
            if not new_emb:
                continue

            # 第一级：向量粗筛
            candidates = [
                (c["id"], c.get("embedding")) for c in all_captures
                if c.get("embedding") and c["id"] != new_cap["id"]
            ]
            if not candidates:
                continue

            scored = [(cid, cosine_similarity(new_emb, emb))
                      for cid, emb in candidates]
            scored.sort(key=lambda x: x[1], reverse=True)
            top_ids = {cid for cid, _ in scored[:top_k]}
            top_captures = [c for c in all_captures if c["id"] in top_ids]

            if not top_captures:
                continue

            # 第二级：LLM 精筛
            try:
                filtered = self._llm_filter(new_cap, top_captures)
            except Exception as e:
                logger.warning(f"Theme LLM filter failed: {e}")
                continue

            strong = [f for f in filtered if f.get("connection_type") == "strong"]
            if not strong:
                continue

            # 第三级：全文确认（仅确认 strong 的）
            confirmed = self._llm_confirm(new_cap, [
                c for c in top_captures
                if c["id"] in {f["capture_id"] for f in strong}
            ])
            if confirmed:
                clusters.append({
                    "new_capture": new_cap,
                    "related_captures": [
                        c for c in top_captures
                        if c["id"] in confirmed
                    ],
                })

        return clusters

    def generate_question(self, cluster: dict) -> str:
        """根据聚类生成自然语言提问。"""
        new = cluster["new_capture"]
        related = cluster["related_captures"]

        theme = new.get("title") or new.get("summary", "")[:30]
        items = []
        items.append(f"1. 《{new.get('title') or '新内容'}》：{new.get('summary', '')[:80]}")
        for i, c in enumerate(related, 2):
            items.append(f"{i}. 《{c.get('title') or '无标题'}》：{c.get('summary', '')[:80]}")

        prompt = QUESTION_PROMPT.format(
            theme=theme,
            items_text="\n".join(items),
        )
        return self._call_llm(prompt, max_tokens=300)

    def generate_theme_name(self, captures: list[dict]) -> str:
        """Generate a natural-language theme name from a list of captures."""
        captures_text = "\n".join(
            f"- 《{c.get('title') or '无标题'}》：{c.get('summary', '')[:80]}"
            for c in captures
        )
        prompt = THEME_NAME_PROMPT.format(captures_text=captures_text)
        raw = self._call_llm(prompt, max_tokens=100)
        return raw.strip().strip('"').strip("'")

    def build_moc(self, theme_name: str, captures: list[dict]) -> str:
        """Build a MOC (Map of Content) markdown page for a theme."""
        captures_text = "\n".join(
            f"- 《{c.get('title') or '无标题'}》：{c.get('summary', '')[:80]} tags={json.dumps(c.get('tags', []))}"
            for c in captures
        )
        prompt = MOC_CONTENT_PROMPT.format(
            theme_name=theme_name, captures_text=captures_text,
        )
        raw = self._call_llm(prompt, max_tokens=500)

        description = ""
        open_questions: list[str] = []
        try:
            parsed = json.loads(raw.strip().lstrip("```json").rstrip("```"))
            description = parsed.get("description", "")
            open_questions = parsed.get("open_questions", [])
        except (json.JSONDecodeError, AttributeError):
            logger.warning("MOC content LLM returned invalid JSON")

        # Build markdown body.
        lines = [
            f"# {theme_name}",
            "",
            description,
            "",
            "## 相关笔记",
        ]
        for i, c in enumerate(captures, 1):
            title = c.get("title") or "无标题"
            summary = c.get("summary", "")[:80]
            line = f"{i}. [[{title}]]"
            if summary:
                line += f" — {summary}"
            lines.append(line)

        if open_questions:
            lines.append("")
            lines.append("## 开放问题")
            for q in open_questions:
                lines.append(f"- {q}")

        lines.append("")
        return "\n".join(lines)

    def graph_report(self, vault_path) -> str:
        """Generate a knowledge graph health report.

        Scans all captures and themes in the vault, computes statistics:
        total captures, total themes, connected %, orphans, bridges.
        """
        from pathlib import Path
        import re
        from agents.markdown_writer import read_note

        vault = Path(vault_path)
        caps_dir = vault / "captures"
        themes_dir = vault / "themes"

        wiki_link_re = re.compile(r"\[\[([^\]]+)\]\]")

        # Collect captures.
        captures: list[dict] = []
        if caps_dir.exists():
            for md_file in caps_dir.glob("*.md"):
                result = read_note(vault, f"captures/{md_file.name}")
                if not result:
                    continue
                frontmatter, body = result
                links = wiki_link_re.findall(body)
                captures.append({
                    "title": frontmatter.get("title") or md_file.stem,
                    "tags": frontmatter.get("tags") or [],
                    "body_links": set(links),
                })

        total_captures = len(captures)

        # Collect themes and which captures they reference.
        themes: list[dict] = []
        # title -> set of theme_names that reference it
        title_to_themes: dict[str, set[str]] = {}
        if themes_dir.exists():
            for md_file in themes_dir.glob("*.md"):
                result = read_note(vault, f"themes/{md_file.name}")
                if not result:
                    continue
                frontmatter, body = result
                theme_name = frontmatter.get("theme_name") or md_file.stem
                cap_count = frontmatter.get("captures") or 0
                linked_titles = wiki_link_re.findall(body)
                themes.append({
                    "theme_name": theme_name,
                    "captures": cap_count,
                    "linked_titles": set(linked_titles),
                })
                for title in linked_titles:
                    title_to_themes.setdefault(title, set()).add(theme_name)

        total_themes = len(themes)

        # Orphans: captures with no [[wiki-link]] in body.
        orphans = [c for c in captures if not c["body_links"]]

        # Connected %.
        connected = total_captures - len(orphans)
        connected_pct = (connected * 100 // total_captures) if total_captures else 0

        # Bridges: captures whose title appears in ≥2 different MOCs.
        bridges: list[tuple[str, list[str]]] = []
        for c in captures:
            title = c["title"]
            if title in title_to_themes and len(title_to_themes[title]) >= 2:
                bridges.append((title, sorted(title_to_themes[title])))

        # Build report.
        lines = ["📊 知识图谱周报："]
        lines.append(f"- 总笔记: {total_captures} 篇")
        lines.append(f"- 主题: {total_themes} 个")
        if themes:
            theme_names = ", ".join(t["theme_name"] for t in themes[:6])
            if len(themes) > 6:
                theme_names += f" 等 {len(themes)} 个"
            lines.append(f"  ({theme_names})")
        lines.append(f"- 已连接: {connected} 篇 ({connected_pct}%)")

        if orphans:
            lines.append(f"- 孤岛笔记: {len(orphans)} 篇 — 建议关联")
            for c in orphans[:5]:
                lines.append(f"  · 《{c['title']}》")
        else:
            lines.append("- 孤岛笔记: 0 篇 ✓")

        if bridges:
            lines.append("- 桥接笔记:")
            for title, theme_names in bridges[:5]:
                themes_str = " ↔ ".join(theme_names)
                lines.append(f"  · [[{title}]] ({themes_str})")

        return "\n".join(lines)

    def match_to_themes(self, vault_path, new_cap: dict,
                        min_tag_overlap: int = 2) -> str | None:
        """Match a capture to an existing MOC by tag overlap.

        Returns the MOC relative path (e.g. ``themes/ai.md``) if a match
        is found, ``None`` otherwise.
        """
        from pathlib import Path
        from agents.markdown_writer import read_moc

        themes_dir = Path(vault_path) / "themes"
        if not themes_dir.exists():
            return None

        cap_tags = set(new_cap.get("tags") or [])
        if not cap_tags:
            return None

        best_path: str | None = None
        best_overlap = 0

        for md_file in themes_dir.glob("*.md"):
            result = read_moc(vault_path, f"themes/{md_file.name}")
            if not result:
                continue
            frontmatter, _body = result
            moc_tags = set(frontmatter.get("tags") or [])
            overlap = len(cap_tags & moc_tags)
            if overlap > best_overlap and overlap >= min_tag_overlap:
                best_overlap = overlap
                best_path = f"themes/{md_file.name}"

        return best_path

    def _llm_filter(self, new_cap: dict, candidates: list[dict]) -> list[dict]:
        candidates_text = "\n".join(
            f"- id={c['id']} summary={c.get('summary', '')[:80]} tags={json.dumps(c.get('tags', []))}"
            for c in candidates
        )
        prompt = THEME_PROMPT.format(
            new_summary=new_cap.get("summary", ""),
            new_tags=json.dumps(new_cap.get("tags", [])),
            candidates_text=candidates_text,
        )
        raw = self._call_llm(prompt, max_tokens=1000)
        return json.loads(raw.strip().lstrip("```json").rstrip("```"))

    def _llm_confirm(self, new_cap: dict, strong_candidates: list[dict]) -> list[str]:
        """全文确认——读 cleaned_text 对比。返回确认相关的 capture IDs。"""
        texts = [f"新内容：{new_cap.get('cached_text', '')[:3000]}"]
        for c in strong_candidates:
            texts.append(f"候选{c['id'][:8]}：{c.get('cached_text', '')[:3000]}")
        prompt = "\n\n---\n\n".join(texts)
        prompt += "\n\n以上新内容与各候选内容是否确实在讲同一主题？只输出确认相关的 capture_id 列表 JSON 数组：[\"xxx\", \"yyy\"]"

        raw = self._call_llm(prompt, max_tokens=200)
        try:
            return json.loads(raw.strip().lstrip("```json").rstrip("```"))
        except json.JSONDecodeError:
            return []

    def _call_llm(self, prompt: str, max_tokens: int = 1000) -> str:
        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json",
        }
        body: dict = {
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


def create_pending_question(repo, agent_type: str, question_text: str,
                            context_ref: str,
                            related_capture_ids: list[str]) -> dict:
    """在 pending_questions 表创建一条待回复提问。"""
    import sqlite3
    qid = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    expires = datetime.now(timezone.utc).replace(hour=23, minute=59).isoformat()
    repo.conn.execute(
        """INSERT INTO pending_questions (id, agent_type, question_text,
           context_ref, status, related_capture_ids, created_at, expires_at)
           VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
        (qid, agent_type, question_text, context_ref,
         json.dumps(related_capture_ids), now, expires),
    )
    repo.conn.commit()
    return {"id": qid, "question_text": question_text, "expires_at": expires}
