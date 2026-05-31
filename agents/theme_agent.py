"""Theme Agent — 事件驱动聚类发现 + 渐进式三级过滤 + 提问生成."""
from __future__ import annotations

import json
import logging
import re
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
{{
  "description": "2-3 句话描述这个主题的核心问题和探讨方向",
  "relation_groups": [
    {{
      "title": "这个分组在主题中的角色，例如 方法论底座/工程实现/产品变化",
      "insight": "一句话说明这一组材料之间的共同关系",
      "notes": [
        {{"title": "必须匹配相关笔记标题", "role": "这篇笔记在该主题里扮演什么角色"}}
      ]
    }}
  ],
  "key_connections": ["2-4 条直接说明文章之间如何互相补充/递进/冲突"],
  "open_questions": ["2-3 个值得思考的开放性问题"]
}}"""

QUESTION_PROMPT = """根据以下聚类信息，生成一条自然的中文提问。

你的任务是用友好的语气告诉用户：TA 最近存的几篇文章之间有关联，
先简要说明这些内容为什么有关联，再给出 2-3 个"可沉淀方向"。
这些方向可以是方法、注意事项、观点、事实或问题，但这是你给用户的建议，
不要让用户先判断这些内容到底属于哪种卡片类型。

聚类主题：{theme}
关联条目：
{items_text}

要求：
1. 先用 1-2 句话说明这些材料的共同问题、互补关系或张力
2. 列出"可沉淀方向："并给 2-3 个选项，例如"方法：..."、"注意：..."、"观点：..."
3. 这些类型只是你的建议，不要让用户先判断卡片类型
4. 结尾问"要我先展开哪个？也可以让我先展开最值得保留的那个。"
5. 用"你"称呼用户

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
            strong_captures = [
                c for c in top_captures
                if c["id"] in {f["capture_id"] for f in strong}
            ]
            confirmed = self._llm_confirm(new_cap, strong_captures)
            if confirmed is None:
                confirmed = [c["id"] for c in strong_captures]
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
        name = raw.strip().strip('"').strip("'")
        if not name or name.lower() in {"untitled", "无标题"}:
            return _fallback_theme_name(captures)
        return name

    def build_moc(self, theme_name: str, captures: list[dict]) -> str:
        """Build a MOC (Map of Content) markdown page for a theme."""
        captures_text = "\n".join(
            f"- 《{c.get('title') or '无标题'}》：{c.get('summary', '')[:80]} tags={json.dumps(c.get('tags', []))}"
            for c in captures
        )
        prompt = MOC_CONTENT_PROMPT.format(
            theme_name=theme_name, captures_text=captures_text,
        )
        raw = self._call_llm(prompt, max_tokens=1000)

        description = ""
        relation_groups: list[dict] = []
        key_connections: list[str] = []
        open_questions: list[str] = []
        try:
            parsed = _parse_json_payload(raw)
            description = parsed.get("description", "")
            relation_groups = parsed.get("relation_groups", [])
            key_connections = parsed.get("key_connections", [])
            open_questions = parsed.get("open_questions", [])
        except (json.JSONDecodeError, AttributeError, ValueError):
            logger.warning("MOC content LLM returned invalid JSON")

        lines = [
            f"# {theme_name}",
            "",
            description or _fallback_theme_description(theme_name, captures),
            "",
            "## 主题脉络",
        ]
        lines.extend(_render_relation_groups(relation_groups, captures))

        lines.extend(["", "## 关键联系"])
        if key_connections:
            for connection in key_connections:
                lines.append(f"- {connection}")
        else:
            lines.extend(_fallback_key_connections(captures))

        lines.extend(["", "## 相关笔记引用"])
        for c in captures:
            title = c.get("title") or "无标题"
            lines.append(f"- [[{title}]]")

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
        raw = self._call_llm(prompt, max_tokens=1800)
        parsed = _parse_json_payload(raw)
        return parsed if isinstance(parsed, list) else []

    def _llm_confirm(self, new_cap: dict, strong_candidates: list[dict]) -> list[str] | None:
        """全文确认——读 cleaned_text 对比。返回确认相关的 capture IDs。"""
        texts = [f"新内容：{_confirm_text(new_cap)}"]
        for c in strong_candidates:
            texts.append(f"候选{c['id'][:8]}：{_confirm_text(c)}")
        prompt = "\n\n---\n\n".join(texts)
        prompt += "\n\n以上新内容与各候选内容是否确实在讲同一主题？只输出确认相关的 capture_id 列表 JSON 数组：[\"xxx\", \"yyy\"]"

        raw = self._call_llm(prompt, max_tokens=200)
        try:
            parsed = _parse_json_payload(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, list) else []

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


def _parse_json_payload(raw: str):
    """Parse JSON even when the model wraps it in prose or a fenced block."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    starts = [index for index, char in enumerate(text) if char in "[{"]
    for start in starts:
        try:
            parsed, _end = decoder.raw_decode(text[start:])
            return parsed
        except json.JSONDecodeError:
            continue
    raise ValueError("No JSON payload found")


def _confirm_text(capture: dict) -> str:
    return (
        str(capture.get("index_text") or "").strip()
        or str(capture.get("summary") or "").strip()
        or str(capture.get("cached_text") or "").strip()[:1200]
    )


def _fallback_theme_name(captures: list[dict]) -> str:
    tags: set[str] = set()
    for capture in captures:
        tags.update(str(tag) for tag in capture.get("tags") or [])

    if "ai-agent" in tags and (
        "engineering-management" in tags or "ai-product" in tags
    ):
        return "AI Agent工程化"
    if "ai-agent" in tags:
        return "AI Agent实践"
    if "knowledge-management" in tags:
        return "知识管理实践"
    if "ai-product" in tags:
        return "AI产品观察"

    for capture in captures:
        title = str(capture.get("title") or "").strip()
        if title and title.lower() not in {"untitled", "无标题"}:
            return title[:18]
    return "未命名主题"


def _fallback_theme_description(theme_name: str, captures: list[dict]) -> str:
    return f"这个主题整理了 {len(captures)} 篇与「{theme_name}」相关的笔记，帮助快速查看材料之间的联系。"


def _render_relation_groups(groups: list[dict], captures: list[dict]) -> list[str]:
    by_title = {str(c.get("title") or "无标题"): c for c in captures}
    rendered: list[str] = []
    used: set[str] = set()

    for group in groups:
        title = str(group.get("title") or "").strip()
        notes = group.get("notes") or []
        if not title or not notes:
            continue
        rendered.extend(["", f"### {title}"])
        insight = str(group.get("insight") or "").strip()
        if insight:
            rendered.extend(["", insight])
        for note in notes:
            note_title = str(note.get("title") or "").strip()
            if not note_title or note_title not in by_title:
                continue
            role = str(note.get("role") or "").strip()
            summary = str(by_title[note_title].get("summary") or "")[:80]
            detail = role or summary
            line = f"- [[{note_title}]]"
            if detail:
                line += f" — {detail}"
            rendered.append(line)
            used.add(note_title)

    remaining = [c for c in captures if str(c.get("title") or "无标题") not in used]
    if remaining:
        rendered.extend(["", "### 核心材料"])
        for capture in remaining:
            title = capture.get("title") or "无标题"
            summary = str(capture.get("summary") or "")[:80]
            line = f"- [[{title}]]"
            if summary:
                line += f" — {summary}"
            rendered.append(line)

    return rendered or ["", "### 核心材料"]


def _fallback_key_connections(captures: list[dict]) -> list[str]:
    if len(captures) < 2:
        return ["- 这篇笔记构成该主题的起点。"]
    first = captures[0].get("title") or "第一篇笔记"
    rest_links = "、".join(
        f"[[{str(c.get('title') or '其他笔记')}]]" for c in captures[1:3]
    )
    return [f"- [[{first}]] 提供主题入口，{rest_links} 补充案例、方法或边界。"]


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
