"""Build reusable embedding index text from committed capture notes."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from agents.managed_sections import is_managed_section_title


IDENTITY_BODY_HINT_CHARS = 500
SECTION_BODY_CHARS = 1600
# Long sections are split instead of clipped: Chinese long-form articles put
# conclusions and action items at the end, which a head-truncation silently
# dropped from the index.
SECTION_CHUNK_CHARS = 1500
SECTION_CHUNK_OVERLAP = 200
# Articles without any ## / ### heading used to produce zero section vectors.
PARAGRAPH_FALLBACK_MIN_CHARS = 1000
PARAGRAPH_FALLBACK_CHUNK_CHARS = 1300


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
    *,
    outline: list[dict] | None = None,
) -> list[IndexItem]:
    sections = _markdown_sections(body)
    if not sections:
        sections = _fallback_sections(body)
    title = _single_line(str(frontmatter.get("title") or "未命名"))
    items: list[IndexItem] = []
    for idx, (section_title, raw_body) in enumerate(sections, 1):
        clean_title = _single_line(section_title)
        if not clean_title:
            continue
        chunks = _split_long_body(raw_body)
        for part, chunk in enumerate(chunks, 1):
            clean_body = _clip(_normalize_body(chunk), SECTION_BODY_CHARS)
            if not clean_body:
                continue
            # The chunk suffix belongs to the *index* text only: the visible
            # section title is what citations show, and "第二部分（第 2 部分）"
            # leaked that internal numbering into the UI.
            index_title = (
                clean_title if part == 1 else f"{clean_title}（第 {part} 部分）"
            )
            section_id = f"section-{idx}" if part == 1 else f"section-{idx}-p{part}"
            index_text = "\n".join(
                [
                    f"文章：{title}",
                    f"章节：{index_title}",
                    f"内容：{clean_body}",
                ]
            )
            items.append(
                IndexItem(
                    capture_id=capture_id,
                    kind="section",
                    section_id=section_id,
                    section_title=clean_title,
                    index_text=index_text,
                    source_hash=_hash_text(index_text),
                    file_path=file_path,
                )
            )
    return items


def _split_long_body(
    raw_body: str,
    *,
    chunk_chars: int = SECTION_CHUNK_CHARS,
    overlap: int = SECTION_CHUNK_OVERLAP,
) -> list[str]:
    """Split one section into overlapping chunks on paragraph boundaries."""
    text = (raw_body or "").strip()
    if not text:
        return []
    if len(_normalize_body(text)) <= SECTION_BODY_CHARS:
        return [text]
    chunks: list[str] = []
    current = ""
    paragraphs: list[str] = []
    for raw_paragraph in re.split(r"\n\s*\n", text):
        raw_paragraph = raw_paragraph.strip()
        if not raw_paragraph:
            continue
        # A single wall-of-text paragraph (common in scraped articles) has no
        # blank line to split on; without this the tail is silently clipped.
        paragraphs.extend(_split_oversized_paragraph(raw_paragraph, chunk_chars))
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if current and len(candidate) > chunk_chars:
            chunks.append(current)
            tail = current[-overlap:].strip() if overlap else ""
            current = f"{tail}\n\n{paragraph}".strip() if tail else paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _split_oversized_paragraph(paragraph: str, chunk_chars: int) -> list[str]:
    """Split one over-long paragraph on sentence ends, then by hard window."""
    if len(paragraph) <= chunk_chars:
        return [paragraph]
    sentences = [
        piece for piece in re.split(r"(?<=[。！？；.!?;])", paragraph) if piece.strip()
    ]
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > chunk_chars:
            if current:
                pieces.append(current)
                current = ""
            for start in range(0, len(sentence), chunk_chars):
                pieces.append(sentence[start : start + chunk_chars])
            continue
        if current and len(current) + len(sentence) > chunk_chars:
            pieces.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        pieces.append(current)
    return [piece.strip() for piece in pieces if piece.strip()]


def _fallback_sections(body: str) -> list[tuple[str, str]]:
    """Give heading-less long text paragraph-sized pseudo sections."""
    text = (body or "").strip()
    if len(_normalize_body(text)) <= PARAGRAPH_FALLBACK_MIN_CHARS:
        return []
    chunks = _split_long_body(
        text, chunk_chars=PARAGRAPH_FALLBACK_CHUNK_CHARS, overlap=0
    )
    sections: list[tuple[str, str]] = []
    for index, chunk in enumerate(chunks, 1):
        lead = re.split(r"(?<=[。！？!?；;\n])", chunk.strip())[0].strip()
        lead = re.sub(r"^#{1,6}\s*", "", lead)[:18].strip()
        # Keep the index prefix so the title stays unique even when two chunks
        # open with the same sentence.
        title = f"正文片段 {index}·{lead}" if lead else f"正文片段 {index}"
        sections.append((title, chunk))
    return sections


def _outline_sections(outline: list[dict]) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    for item in outline:
        section_title = _single_line(str(item.get("title") or ""))
        summary = _single_line(str(item.get("summary") or ""))
        if section_title and summary:
            sections.append((section_title, summary))
    return sections


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
        current_level = len(match.group(1))
        end = len(body)
        for next_match in matches[idx + 1 :]:
            if len(next_match.group(1)) <= current_level:
                end = next_match.start()
                break
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
