"""Build reusable embedding index text from committed capture notes."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from agents.managed_sections import is_managed_section_title


IDENTITY_BODY_HINT_CHARS = 500
SECTION_BODY_CHARS = 1600


@dataclass(frozen=True)
class IndexItem:
    capture_id: str
    kind: str
    section_id: str
    section_title: str
    index_text: str
    source_hash: str
    file_path: str


def build_identity_index(
    capture_id: str,
    file_path: str,
    frontmatter: dict[str, Any],
    body: str,
    *,
    outline: list[dict] | None = None,
) -> IndexItem:
    title = _single_line(str(frontmatter.get("title") or "未命名"))
    story = _single_line(
        str(frontmatter.get("story") or frontmatter.get("summary") or "")
    )
    tags = frontmatter.get("tags") or []
    tag_text = ", ".join(str(tag) for tag in tags)
    outline_text = _outline_text(
        outline if outline is not None else frontmatter.get("outline") or []
    )
    body_hint = _first_useful_paragraph(body)

    lines = [
        f"标题：{title}",
        f"摘要：{story}" if story else "",
        f"标签：{tag_text}" if tag_text else "",
        f"目录纲要：{outline_text}" if outline_text else "",
        f"正文线索：{_clip(body_hint, IDENTITY_BODY_HINT_CHARS)}" if body_hint else "",
    ]
    index_text = "\n".join(line for line in lines if line).strip()
    return IndexItem(
        capture_id=capture_id,
        kind="identity",
        section_id="",
        section_title="",
        index_text=index_text,
        source_hash=_hash_text(index_text),
        file_path=file_path,
    )


def build_section_indexes(
    capture_id: str,
    file_path: str,
    frontmatter: dict[str, Any],
    body: str,
) -> list[IndexItem]:
    sections = _markdown_sections(body)
    title = _single_line(str(frontmatter.get("title") or "未命名"))
    items: list[IndexItem] = []
    for idx, (section_title, section_body) in enumerate(sections, 1):
        clean_title = _single_line(section_title)
        clean_body = _clip(_normalize_body(section_body), SECTION_BODY_CHARS)
        if not clean_title or not clean_body:
            continue
        index_text = "\n".join(
            [
                f"文章：{title}",
                f"章节：{clean_title}",
                f"内容：{clean_body}",
            ]
        )
        items.append(
            IndexItem(
                capture_id=capture_id,
                kind="section",
                section_id=f"section-{idx}",
                section_title=clean_title,
                index_text=index_text,
                source_hash=_hash_text(index_text),
                file_path=file_path,
            )
        )
    return items


def _outline_text(outline: list[dict]) -> str:
    parts: list[str] = []
    for item in outline:
        title = _single_line(str(item.get("title") or ""))
        summary = _single_line(str(item.get("summary") or ""))
        if title and summary:
            parts.append(f"{title}：{summary}")
        elif title:
            parts.append(title)
    return "；".join(parts)


def _first_useful_paragraph(body: str) -> str:
    for block in re.split(r"\n\s*\n", body or ""):
        text = _normalize_body(block)
        if not text:
            continue
        if re.fullmatch(r"#+\s+.+", text):
            continue
        if text.startswith("目录纲要"):
            continue
        return text
    return ""


def _markdown_sections(body: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^(#{2,3})\s+(.+?)\s*$", body or "", re.MULTILINE))
    sections: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        title = match.group(2).strip()
        if is_managed_section_title(title):
            continue
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        section_body = body[start:end].strip()
        sections.append((title, section_body))
    return sections


def _normalize_body(text: str) -> str:
    text = re.sub(r"^#+\s*", "", text.strip())
    return re.sub(r"\s+", " ", text).strip()


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
