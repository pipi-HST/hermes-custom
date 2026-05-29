"""Utility for writing notes as Markdown files with YAML frontmatter."""
from __future__ import annotations

import logging
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NoteMeta:
    id: str
    title: str
    date: str  # YYYY-MM-DD
    created: str  # ISO timestamp
    source: str
    source_type: str
    tags: list[str] = field(default_factory=list)
    story: str = ""
    word_count: int = 0


# Chinese tech terms mapped to English equivalents for slug generation.
_TECH_TERMS: dict[str, str] = {
    "造": "builds",
    "训练": "train",
    "推理": "infer",
    "部署": "deploy",
    "生成": "generate",
    "大模型": "llm",
    "人工智能": "ai",
    "智能体": "agent",
    "机器学习": "machine-learning",
    "深度学习": "deep-learning",
    "神经网络": "neural-network",
    "自然语言": "nlp",
    "知识管理": "knowledge-management",
    "信息": "information",
    "学习": "learning",
    "芯片": "chip",
}


def _make_slug(title: str) -> str:
    """Convert Chinese/English title to ASCII slug.

    Extracts ASCII words from the title and maps common Chinese tech terms
    to their English equivalents. Returns "untitled" if no terms are found.
    """
    if not title or title.isspace():
        return "untitled"

    ascii_pattern = re.compile(r"[A-Za-z]+")
    matches = list(ascii_pattern.finditer(title))

    terms: list[str] = []
    last_end = 0

    for i, m in enumerate(matches):
        # Check Chinese text before this match
        if m.start() > last_end:
            cjk_segment = title[last_end : m.start()]
            for cn, en in _TECH_TERMS.items():
                if cn in cjk_segment:
                    terms.append(en.lower())

        # Add the ASCII word
        terms.append(m.group().lower())
        last_end = m.end()

    # Check remaining suffix after last match
    if last_end < len(title):
        suffix = title[last_end:]
        for cn, en in _TECH_TERMS.items():
            if cn in suffix:
                terms.append(en.lower())

    # If no ASCII words found, try mapping the entire title
    if not terms:
        for cn, en in _TECH_TERMS.items():
            if cn in title:
                terms.append(en.lower())

    # Deduplicate consecutive identical terms
    result: list[str] = []
    for t in terms:
        if result and t == result[-1]:
            continue
        result.append(t)

    if result:
        return "-".join(result)
    return f"note-{hashlib.sha1(title.encode('utf-8')).hexdigest()[:8]}"


def make_note_filename(title: str, date: str, capture_id: str) -> str:
    """Build a readable filename: title first, then date and short id."""
    safe_title = _safe_filename_title(title)
    safe_date = _date_prefix(date)
    short_id = (capture_id or "no-id")[:8]
    return f"{safe_title}-{safe_date}-{short_id}.md"


def _safe_filename_title(title: str) -> str:
    text = (title or "未命名").strip() or "未命名"
    text = re.sub(r'[/\\:*?"<>|？]', "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:40] or "未命名"


def _date_prefix(value: str | None) -> str:
    text = (value or "").strip().strip("'\"")
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if match:
        return match.group(0)
    return datetime.now().strftime("%Y-%m-%d")


def write_note(
    vault_path: Path, meta: NoteMeta, body: str, stage: str = "captures"
) -> str:
    """Write a note as Markdown file with YAML frontmatter.

    Creates ``{vault_path}/{stage}/{title}-{date}-{short_id}.md`` and returns
    the relative path.
    """
    relative_stage = "_archive/raw" if stage == "raw" else stage
    stage_dir = vault_path / relative_stage
    stage_dir.mkdir(parents=True, exist_ok=True)

    filename = make_note_filename(meta.title, meta.date, meta.id)
    filepath = stage_dir / filename

    if stage == "raw":
        frontmatter = {
            "id": meta.id,
            "title": meta.title,
            "date": meta.date,
            "source": meta.source,
            "source_type": meta.source_type,
            "status": "raw",
        }
    else:
        frontmatter = {
            "id": meta.id,
            "title": meta.title,
            "date": meta.date,
            "created": meta.created,
            "source": meta.source,
            "source_type": meta.source_type,
            "tags": meta.tags,
            "story": meta.story,
            "word_count": meta.word_count,
        }

    content = (
        f"---\n{yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)}"
        f"---\n\n{body}"
    )
    filepath.write_text(content, encoding="utf-8")

    return f"{relative_stage}/{filename}"


def read_note(
    vault_path: Path, file_path: str
) -> tuple[dict, str] | None:
    """Read a note file. Returns ``(frontmatter_dict, body_text)`` or ``None``."""
    full_path = vault_path / file_path
    if not full_path.exists():
        return None

    text = full_path.read_text(encoding="utf-8")

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                logger.warning("Malformed YAML frontmatter in %s", file_path)
                return {}, parts[2].strip() if len(parts) > 2 else text
            body = parts[2].strip()
            return frontmatter, body

    return {}, text


_LINK_SECTION_RE = re.compile(r"^##\s+相关笔记\s*$", re.MULTILINE)
_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def append_links(
    vault_path: Path, file_path: str, links: list[dict]
) -> int:
    """Append ``[[title]]`` wiki-links to the ``## 相关笔记`` section of a note.

    Creates the section if absent. Skips links whose title already appears.
    Returns the number of links actually appended, or ``-1`` if the file
    was not found.
    """
    if not links:
        return 0

    full_path = vault_path / file_path
    if not full_path.exists():
        return -1

    text = full_path.read_text(encoding="utf-8")

    # Split frontmatter from body.
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            header = f"---{parts[1]}---"
            body = parts[2]
        else:
            header = ""
            body = text
    else:
        header = ""
        body = text

    # Find or create the related-notes section.
    _next_heading_re = re.compile(r"^##\s+", re.MULTILINE)
    match = _LINK_SECTION_RE.search(body)
    if match:
        section_start = match.end()
        # Find end of this section (next ## heading or end of body).
        next_heading = _next_heading_re.search(body, section_start)
        section_end = next_heading.start() if next_heading else len(body)
        section_text = body[section_start:section_end]
        existing = set(_WIKI_LINK_RE.findall(section_text))
    else:
        section_start = -1
        section_end = -1
        existing = set()

    new_lines: list[str] = []
    for link in links:
        title = link.get("title", "")
        reason = link.get("reason", "")
        if not title or title in existing:
            continue
        line = f"- [[{title}]]"
        if reason:
            line += f" — {reason}"
        new_lines.append(line)
        existing.add(title)

    if not new_lines:
        return 0

    if section_start == -1:
        # Append a new section at the end of the body.
        trailing = "\n" if body and not body.endswith("\n") else ""
        body = f"{body}{trailing}\n## 相关笔记\n\n" + "\n".join(new_lines) + "\n"
    else:
        # Insert new lines at the end of the section (before next heading).
        insert_text = "\n".join(new_lines) + "\n"
        body = body[:section_end] + insert_text + body[section_end:]

    full_path.write_text(f"{header}\n{body}", encoding="utf-8")
    return len(new_lines)


def write_moc(
    vault_path: Path,
    theme_name: str,
    body: str,
    tags: list[str],
    capture_count: int,
) -> str:
    """Write a Map of Content (MOC) page to ``themes/{slug}.md``.

    Returns the relative path (e.g. ``themes/ai-education.md``).
    """
    themes_dir = vault_path / "themes"
    themes_dir.mkdir(parents=True, exist_ok=True)

    slug = _make_slug(theme_name)
    filename = f"{slug}.md"
    filepath = themes_dir / filename

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    frontmatter = {
        "type": "theme",
        "theme_name": theme_name,
        "tags": tags,
        "created": today,
        "captures": capture_count,
    }

    content = (
        f"---\n{yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)}"
        f"---\n\n{body}"
    )
    filepath.write_text(content, encoding="utf-8")

    return f"themes/{filename}"


_CHANGELOG_SECTION_RE = re.compile(r"^##\s+更新日志\s*$", re.MULTILINE)


def read_moc(
    vault_path: Path, file_path: str
) -> tuple[dict, str] | None:
    """Read a MOC file. Returns ``(frontmatter_dict, body_text)`` or ``None``."""
    return read_note(vault_path, file_path)


def update_moc(
    vault_path: Path, file_path: str, capture: dict
) -> bool:
    """Update an existing MOC page with a new capture.

    Appends a wiki-link entry to ``## 相关笔记`` (if not already present),
    adds a changelog entry to ``## 更新日志`` (creating it if needed), and
    increments the ``captures`` count in frontmatter.

    Returns ``True`` if anything was updated, ``False`` otherwise (duplicate
    title or file not found).
    """
    full_path = vault_path / file_path
    if not full_path.exists():
        return False

    text = full_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return False

    parts = text.split("---", 2)
    if len(parts) < 3:
        return False

    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return False

    body = parts[2]

    title = capture.get("title") or "未命名"
    summary = capture.get("summary", "")[:60]

    # Dedup check: does the 相关笔记 section already contain this title?
    match = _LINK_SECTION_RE.search(body)
    if match:
        next_heading = re.compile(r"^##\s+", re.MULTILINE).search(body, match.end())
        section_end = next_heading.start() if next_heading else len(body)
        existing = set(_WIKI_LINK_RE.findall(body[match.end():section_end]))
        if title in existing:
            return False

    # Append to 相关笔记 section.
    new_line = f"- [[{title}]]"
    if summary:
        new_line += f" — {summary}"

    if match:
        # Strip trailing whitespace from the section so new entry is flush.
        before = body[:section_end].rstrip("\n ")
        after = body[section_end:]
        body = before + "\n" + new_line + "\n\n" + after.lstrip("\n")
    else:
        trailing = "\n" if body and not body.endswith("\n") else ""
        body = f"{body}{trailing}\n## 相关笔记\n\n{new_line}\n"

    # Add or update 更新日志 section.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_line = f"- {today}: 新增《{title}》"
    changelog_match = _CHANGELOG_SECTION_RE.search(body)
    if changelog_match:
        insert_pos = changelog_match.end()
        body = body[:insert_pos] + "\n" + log_line + body[insert_pos:]
    else:
        trailing = "\n" if body and not body.endswith("\n") else ""
        body = f"{body}{trailing}\n## 更新日志\n{log_line}\n"

    # Update captures count.
    old_count = frontmatter.get("captures", 0) or 0
    frontmatter["captures"] = old_count + 1

    header = (
        "---\n"
        f"{yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)}"
        "---"
    )
    full_path.write_text(f"{header}\n{body}", encoding="utf-8")
    return True
