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
    outline: list[dict] = field(default_factory=list)


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
    snapshot = read_note_snapshot(vault_path, file_path)
    return snapshot[:2] if snapshot is not None else None


def read_note_snapshot(vault_path: Path, file_path: str) -> tuple[dict, str, str] | None:
    """Read frontmatter, body, and the exact UTF-8 text from one file snapshot."""
    candidate = Path(file_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    try:
        resolved_vault = vault_path.expanduser().resolve()
        full_path = (resolved_vault / candidate).resolve()
        full_path.relative_to(resolved_vault)
    except (OSError, ValueError):
        return None
    try:
        raw_text = full_path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return None

    # Preserve the parsed view's universal newlines without losing the bytes
    # needed for the revision and conditional replacement.
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                logger.warning("Malformed YAML frontmatter in %s", file_path)
                return {}, parts[2].strip(), raw_text
            body = parts[2].strip()
            return frontmatter, body, raw_text

    return {}, text, raw_text


def note_revision(raw_text: str) -> str:
    """Opaque revision for the entire Markdown file, including frontmatter."""
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


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
    return write_theme_page(
        vault_path,
        theme_name=theme_name,
        body=body,
        captures=[],
        garden_cards=[],
        tags=tags,
        capture_count=capture_count,
    )


_HERMES_CHANGELOG_START = "<!-- HERMES-CHANGELOG:START -->"
_HERMES_CHANGELOG_END = "<!-- HERMES-CHANGELOG:END -->"
HERMES_THEME_START = "<!-- HERMES-THEME:START -->"
HERMES_THEME_END = "<!-- HERMES-THEME:END -->"
_SYSTEM_RECORD_HEADING = "## 系统记录"


def _theme_id(theme_name: str) -> str:
    return f"theme_{_make_slug(theme_name)}"


def _theme_capture_paths(captures: list[dict]) -> list[str]:
    paths: list[str] = []
    for capture in captures:
        path = str(capture.get("file_path") or capture.get("path") or "").strip()
        if path and path not in paths:
            paths.append(path)
    return paths


def _theme_card_paths(garden_cards: list[dict] | None) -> list[str]:
    paths: list[str] = []
    for card in garden_cards or []:
        path = str(card.get("path") or card.get("file_path") or "").strip()
        if path and path not in paths:
            paths.append(path)
    return paths


def _strip_theme_h1(body: str, theme_name: str) -> str:
    text = (body or "").strip()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2].strip()
    if HERMES_THEME_START in text and HERMES_THEME_END in text:
        start = text.find(HERMES_THEME_START) + len(HERMES_THEME_START)
        end = text.find(HERMES_THEME_END)
        text = text[start:end].strip()
    h1_re = re.compile(rf"^#\s+{re.escape(theme_name)}\s*$\n?", re.MULTILINE)
    return h1_re.sub("", text, count=1).strip()


def _replace_section(body: str, heading: str, replacement: str) -> str:
    heading_re = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
    match = heading_re.search(body)
    if not match:
        trailing = "\n" if body and not body.endswith("\n") else ""
        return f"{body}{trailing}{replacement.strip()}\n"
    next_heading = re.compile(r"^##\s+", re.MULTILINE).search(body, match.end())
    section_end = next_heading.start() if next_heading else len(body)
    before = body[:match.start()].rstrip("\n")
    after = body[section_end:].lstrip("\n")
    pieces = [piece for piece in (before, replacement.strip(), after) if piece]
    return "\n\n".join(pieces).strip() + "\n"


def _theme_card_section(garden_cards: list[dict] | None) -> str:
    lines = ["## 已沉淀的知识卡片", ""]
    seen_keys: set[str] = set()
    seen_titles: set[str] = set()
    for card in garden_cards or []:
        path = str(card.get("path") or card.get("file_path") or "").strip()
        title = str(card.get("title") or Path(path).stem).strip()
        key = path or title
        if not title or key in seen_keys or title in seen_titles:
            continue
        seen_keys.add(key)
        seen_titles.add(title)
        lines.append(f"- [[{title}]]")
    if len(lines) == 2:
        lines.append("- 暂无")
    return "\n".join(lines)


def _ensure_theme_managed_body(
    theme_name: str,
    body: str,
    garden_cards: list[dict] | None = None,
) -> str:
    text = _strip_theme_h1(body, theme_name)
    required_headings = [
        ("主题判断", "这一组材料的主题判断待补充。"),
        ("相关材料", "- 暂无"),
        ("材料之间的关系", "- 暂无"),
        ("可沉淀的知识卡片方向", "- 暂无"),
    ]
    for heading, fallback in required_headings:
        if not re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.MULTILINE):
            trailing = "\n" if text and not text.endswith("\n") else ""
            text = f"{text}{trailing}\n## {heading}\n\n{fallback}\n".strip()
    text = _replace_section(text, "已沉淀的知识卡片", _theme_card_section(garden_cards))
    return text.strip()


def _extract_user_theme_notes(body: str) -> str:
    match = re.search(r"^##\s+我的补充\s*$", body or "", re.MULTILINE)
    if not match:
        return "## 我的补充\n\n"
    return body[match.start():].strip() + "\n"


def _theme_content(theme_name: str, managed_body: str, user_notes: str) -> str:
    return (
        f"# {theme_name}\n\n"
        f"{HERMES_THEME_START}\n"
        f"{managed_body.strip()}\n"
        f"{HERMES_THEME_END}\n\n"
        f"{user_notes.strip()}\n"
    )


def write_theme_page(
    vault_path: Path,
    theme_name: str,
    body: str,
    captures: list[dict],
    garden_cards: list[dict] | None = None,
    tags: list[str] | None = None,
    capture_count: int | None = None,
) -> str:
    """Write a Theme View Markdown export with a managed Hermes block."""
    themes_dir = vault_path / "themes"
    themes_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{_make_slug(theme_name)}.md"
    filepath = themes_dir / filename
    now = datetime.now(timezone.utc)
    frontmatter = {
        "type": "theme",
        "status": "active",
        "theme_id": _theme_id(theme_name),
        "theme_name": theme_name,
        "updated": now.isoformat(),
        "last_scanned": now.isoformat(),
        "captures": len(captures) if capture_count is None else capture_count,
        "related_captures": _theme_capture_paths(captures),
        "related_garden_cards": _theme_card_paths(garden_cards),
    }
    open_questions = _extract_open_questions(body)
    if open_questions:
        frontmatter["open_questions"] = open_questions
    if tags:
        frontmatter["tags"] = tags

    managed_body = _ensure_theme_managed_body(theme_name, body, garden_cards)
    user_notes = _extract_user_theme_notes(body)
    content = (
        f"---\n{yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)}"
        f"---\n\n{_theme_content(theme_name, managed_body, user_notes)}"
    )
    filepath.write_text(content, encoding="utf-8")
    return f"themes/{filename}"


def update_theme_page(
    vault_path: Path,
    file_path: str,
    theme_name: str,
    body: str,
    captures: list[dict],
    garden_cards: list[dict] | None = None,
    tags: list[str] | None = None,
) -> bool:
    """Update only the managed section and owned frontmatter of a Theme page."""
    full_path = vault_path / file_path
    if not full_path.exists():
        return False

    text = full_path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                return False
            old_body = parts[2]
        else:
            frontmatter = {}
            old_body = text
    else:
        frontmatter = {}
        old_body = text

    now = datetime.now(timezone.utc).isoformat()
    frontmatter.update({
        "type": "theme",
        "status": frontmatter.get("status") or "active",
        "theme_id": frontmatter.get("theme_id") or _theme_id(theme_name),
        "theme_name": theme_name,
        "updated": now,
        "last_scanned": now,
        "captures": len(captures),
        "related_captures": _theme_capture_paths(captures),
        "related_garden_cards": _theme_card_paths(garden_cards),
    })
    if tags is not None:
        frontmatter["tags"] = tags

    managed_body = _ensure_theme_managed_body(theme_name, body, garden_cards)
    user_notes = _extract_user_theme_notes(old_body)
    content = (
        f"---\n{yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)}"
        f"---\n\n{_theme_content(theme_name, managed_body, user_notes)}"
    )
    full_path.write_text(content, encoding="utf-8")
    return True


def _theme_managed_body_from_existing(body: str, theme_name: str) -> str:
    if HERMES_THEME_START in body and HERMES_THEME_END in body:
        start = body.find(HERMES_THEME_START) + len(HERMES_THEME_START)
        end = body.find(HERMES_THEME_END)
        if start < end:
            return body[start:end].strip()
    return _strip_theme_h1(body, theme_name)


def append_garden_card_to_theme_page(
    vault_path: Path,
    file_path: str,
    *,
    card_title: str,
    card_path: str,
) -> bool:
    """Append a committed Knowledge Card link to a Theme page."""
    full_path = vault_path / file_path
    if not full_path.exists():
        return False

    text = full_path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                return False
            old_body = parts[2]
        else:
            frontmatter = {}
            old_body = text
    else:
        frontmatter = {}
        old_body = text

    theme_name = str(frontmatter.get("theme_name") or Path(file_path).stem).strip()
    if not theme_name:
        return False

    existing_paths = _theme_card_paths([
        {"path": path} for path in frontmatter.get("related_garden_cards") or []
    ])
    if card_path and card_path not in existing_paths:
        existing_paths.append(card_path)
    frontmatter["related_garden_cards"] = existing_paths
    frontmatter["updated"] = datetime.now(timezone.utc).isoformat()

    managed_body = _theme_managed_body_from_existing(old_body, theme_name)
    existing_titles = [
        {"title": title, "path": title}
        for title in _WIKI_LINK_RE.findall(managed_body)
        if title
    ]
    garden_cards = existing_titles + [{"title": card_title, "path": card_path}]
    managed_body = _replace_section(
        managed_body,
        "已沉淀的知识卡片",
        _theme_card_section(garden_cards),
    )
    user_notes = _extract_user_theme_notes(old_body)
    content = (
        f"---\n{yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)}"
        f"---\n\n{_theme_content(theme_name, managed_body, user_notes)}"
    )
    full_path.write_text(content, encoding="utf-8")
    return True


def read_moc(
    vault_path: Path, file_path: str
) -> tuple[dict, str] | None:
    """Read a MOC file. Returns ``(frontmatter_dict, body_text)`` or ``None``."""
    return read_note(vault_path, file_path)


def _extract_open_questions(body: str) -> list[str]:
    match = re.search(r"^##\s+开放问题\s*$", body, re.MULTILINE)
    if not match:
        return []

    next_heading = re.compile(r"^##\s+", re.MULTILINE).search(body, match.end())
    section_end = next_heading.start() if next_heading else len(body)
    section = body[match.end():section_end]
    questions: list[str] = []
    for line in section.splitlines():
        text = re.sub(r"^\s*[-*]\s+", "", line).strip()
        if text:
            questions.append(text)
    return questions


def _append_to_managed_block(
    body: str,
    start_marker: str,
    end_marker: str,
    heading: str,
    line: str,
) -> str:
    start = body.find(start_marker)
    end = body.find(end_marker)
    if start != -1 and end != -1 and start < end:
        before = body[:end].rstrip("\n")
        after = body[end:]
        return f"{before}\n{line}\n{after}"

    managed_match = re.search(r"^##\s+(?:系统记录|Hermes 管理)\s*$", body, re.MULTILINE)
    if managed_match:
        next_heading = re.compile(r"^##\s+", re.MULTILINE).search(
            body, managed_match.end()
        )
        insert_pos = next_heading.start() if next_heading else len(body)
        before = body[:insert_pos].rstrip("\n")
        after = body[insert_pos:]
        return (
            f"{before}\n{start_marker}\n{heading}\n{line}\n{end_marker}\n"
            f"{after}"
        )

    trailing = "\n" if body and not body.endswith("\n") else ""
    return (
        f"{body}{trailing}\n{_SYSTEM_RECORD_HEADING}\n"
        f"{start_marker}\n{heading}\n{line}\n{end_marker}\n"
    )


def _append_to_section(body: str, heading: str, line: str) -> str:
    heading_re = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
    match = heading_re.search(body)
    if not match:
        trailing = "\n" if body and not body.endswith("\n") else ""
        return f"{body}{trailing}\n## {heading}\n{line}\n"

    next_heading = re.compile(r"^##\s+", re.MULTILINE).search(body, match.end())
    section_end = next_heading.start() if next_heading else len(body)
    before = body[:section_end].rstrip("\n")
    after = body[section_end:]
    return f"{before}\n{line}\n{after}"


def _append_to_theme_trail(body: str, line: str) -> str:
    heading = "主题脉络"
    heading_re = re.compile(rf"^##\s+{heading}\s*$", re.MULTILINE)
    match = heading_re.search(body)
    if not match:
        insert = f"## {heading}\n\n### 后续发现\n{line}\n"
        return _insert_before_reference_or_system(body, insert)

    next_heading = re.compile(r"^##\s+", re.MULTILINE).search(body, match.end())
    section_end = next_heading.start() if next_heading else len(body)
    section = body[match.end():section_end]
    subheading_match = re.search(r"^###\s+后续发现\s*$", section, re.MULTILINE)
    if subheading_match:
        before = body[:section_end].rstrip("\n")
        after = body[section_end:]
        return f"{before}\n{line}\n{after}"

    before = body[:section_end].rstrip("\n")
    after = body[section_end:]
    return f"{before}\n\n### 后续发现\n{line}\n{after}"


def _insert_before_reference_or_system(body: str, insert: str) -> str:
    target = re.search(r"^##\s+(?:相关笔记引用|系统记录|Hermes 管理)\s*$", body, re.MULTILINE)
    if target:
        before = body[:target.start()].rstrip("\n")
        after = body[target.start():]
        return f"{before}\n\n{insert}\n{after}"
    trailing = "\n" if body and not body.endswith("\n") else ""
    return f"{body}{trailing}\n{insert}"


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

    existing = set(_WIKI_LINK_RE.findall(body))
    if title in existing:
        return False

    new_line = f"- [[{title}]]"
    if summary:
        new_line += f" — {summary}"

    body = _append_to_theme_trail(body, new_line)
    body = _append_to_section(body, "相关笔记引用", f"- [[{title}]]")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_line = f"- {today}: 新增《{title}》"
    body = _append_to_managed_block(
        body,
        _HERMES_CHANGELOG_START,
        _HERMES_CHANGELOG_END,
        "### 更新日志",
        log_line,
    )

    # Update captures count.
    old_count = frontmatter.get("captures", 0) or 0
    frontmatter["captures"] = old_count + 1
    frontmatter["last_scanned"] = datetime.now(timezone.utc).isoformat()

    header = (
        "---\n"
        f"{yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)}"
        "---"
    )
    full_path.write_text(f"{header}\n{body}", encoding="utf-8")
    return True
