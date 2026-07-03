#!/usr/bin/env python3
"""Verify a committed Capture Body against the long-form directory contract."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.library_agent import (  # noqa: E402
    _directory_block_offsets,
    _heading_anchor_id,
    _is_capture_processing_note_line,
    _is_directory_item,
    _is_non_content_section_title,
    _read_markdown_parts,
    _single_line,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-id", required=True)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--expected-section-count", type=int)
    args = parser.parse_args()

    report = verify_capture_body_contract(
        capture_id=args.capture_id,
        db_path=args.db.expanduser(),
        vault_path=args.vault.expanduser(),
        expected_section_count=args.expected_section_count,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def verify_capture_body_contract(
    *,
    capture_id: str,
    db_path: Path,
    vault_path: Path,
    expected_section_count: int | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    capture, lookup_warning = _lookup_capture(conn, vault_path, capture_id)
    if capture is None:
        return {
            "ok": False,
            "capture_id": capture_id,
            "errors": ["capture_not_found"],
        }

    file_path = str(capture["file_path"] or "")
    raw_path = str(capture["raw_path"] or "")
    resolved_capture_id = str(capture["id"] or "")
    markdown_path = _resolve_vault_path(vault_path, file_path)
    raw_markdown_path = _resolve_vault_path(vault_path, raw_path)

    formal_body = ""
    raw_body = ""
    if not markdown_path.exists():
        errors.append("formal_markdown_missing")
    else:
        parsed = _read_markdown_parts(markdown_path)
        if parsed is None:
            errors.append("formal_markdown_parse_failed")
        else:
            _frontmatter, formal_body = parsed
    if not raw_markdown_path.exists():
        errors.append("raw_markdown_missing")
    else:
        parsed_raw = _read_markdown_parts(raw_markdown_path)
        raw_body = parsed_raw[1] if parsed_raw is not None else raw_markdown_path.read_text(encoding="utf-8")

    directory_items = _parse_directory_items(formal_body)
    content_headings = _parse_content_headings(formal_body)
    expected_directory_items = [
        {"title": item["title"], "href": f"#{item['anchor']}"}
        for item in content_headings
    ]
    directory_relative_links_only = bool(directory_items) and all(
        str(item.get("href") or "").startswith("#") for item in directory_items
    )
    directory_absolute_links = [
        item for item in directory_items if _is_absolute_or_local_url(str(item.get("href") or ""))
    ]
    directory_matches_headings = directory_items == expected_directory_items
    has_processing_note = _has_processing_note(formal_body)

    committed_rows = conn.execute(
        """SELECT id, status, draft_text, word_count
           FROM capture_drafts
           WHERE committed_capture_id = ?
           ORDER BY updated_at DESC, created_at DESC""",
        (resolved_capture_id,),
    ).fetchall()
    committed_draft_count = len(committed_rows)
    committed_draft_synced = bool(committed_rows) and all(
        str(row["draft_text"] or "") == formal_body
        and int(row["word_count"] or 0) == len(formal_body)
        for row in committed_rows
    )

    metadata = conn.execute(
        "SELECT outline_json FROM capture_metadata WHERE capture_id = ?",
        (resolved_capture_id,),
    ).fetchone()
    outline_json = str(metadata["outline_json"] or "") if metadata else ""
    outline_metadata_ready = _outline_metadata_ready(outline_json)
    section_embedding_count = int(
        conn.execute(
            """SELECT COUNT(*) FROM capture_embeddings
               WHERE capture_id = ? AND kind = 'section'""",
            (resolved_capture_id,),
        ).fetchone()[0]
    )

    if not directory_items:
        errors.append("directory_missing_or_unparseable")
    if directory_items and not directory_relative_links_only:
        errors.append("directory_links_not_relative")
    if directory_absolute_links:
        errors.append("directory_contains_absolute_url")
    if directory_items and not directory_matches_headings:
        errors.append("directory_titles_or_anchors_do_not_match_headings")
    if has_processing_note:
        errors.append("capture_body_contains_processing_note")
    if not committed_rows:
        errors.append("committed_draft_missing")
    elif not committed_draft_synced:
        errors.append("committed_draft_not_synced")
    if not outline_metadata_ready:
        errors.append("outline_metadata_missing")
    if expected_section_count is not None and section_embedding_count != expected_section_count:
        errors.append("section_embedding_count_mismatch")
    elif expected_section_count is None and content_headings and section_embedding_count != len(content_headings):
        errors.append("section_embedding_count_does_not_match_heading_count")

    return {
        "ok": not errors,
        "requested_capture_id": capture_id,
        "resolved_capture_id": resolved_capture_id,
        "capture_id": resolved_capture_id,
        "lookup_warning": lookup_warning,
        "file_path": file_path,
        "raw_path": raw_path,
        "markdown_path": str(markdown_path),
        "raw_markdown_path": str(raw_markdown_path),
        "directory_items": directory_items,
        "content_headings": content_headings,
        "directory_relative_links_only": directory_relative_links_only,
        "directory_absolute_links": directory_absolute_links,
        "directory_matches_headings": directory_matches_headings,
        "has_processing_note": has_processing_note,
        "raw_has_processing_note": _has_processing_note(raw_body),
        "committed_draft_count": committed_draft_count,
        "committed_draft_synced": committed_draft_synced,
        "outline_metadata_ready": outline_metadata_ready,
        "section_embedding_count": section_embedding_count,
        "expected_section_count": expected_section_count,
        "errors": errors,
    }


def _lookup_capture(
    conn: sqlite3.Connection,
    vault_path: Path,
    capture_id: str,
) -> tuple[sqlite3.Row | None, str]:
    row = conn.execute(
        "SELECT id, file_path, raw_path FROM captures WHERE id = ?",
        (capture_id,),
    ).fetchone()
    if row is not None:
        return row, ""

    backup_title = _title_from_backup_for_missing_id(vault_path, capture_id)
    if not backup_title:
        return None, ""
    rows = conn.execute(
        """SELECT id, file_path, raw_path FROM captures
           WHERE file_path LIKE ?
           ORDER BY updated_at DESC, created_at DESC
           LIMIT 2""",
        (f"%{backup_title}-%",),
    ).fetchall()
    if len(rows) == 1:
        return rows[0], f"requested id not found; resolved by backup title: {backup_title}"
    return None, f"requested id not found; backup title lookup ambiguous: {backup_title}"


def _title_from_backup_for_missing_id(vault_path: Path, capture_id: str) -> str:
    token = capture_id[:8]
    if not token:
        return ""
    captures_dir = vault_path / "captures"
    if not captures_dir.exists():
        return ""
    for path in captures_dir.glob(f"*{token}*.md.bak*"):
        match = re.match(r"(.+)-\d{4}-\d{2}-\d{2}-[0-9a-f]{8}\.md", path.name)
        if match:
            return match.group(1)
    return ""


def _resolve_vault_path(vault_path: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else vault_path / path


def _parse_directory_items(body: str) -> list[dict[str, str]]:
    start, end = _directory_block_offsets(body)
    if start < 0:
        return []
    items: list[dict[str, str]] = []
    for line in body[start:end].splitlines():
        stripped = line.strip()
        if not _is_directory_item(stripped):
            continue
        value = stripped.split(None, 1)[1].strip() if " " in stripped else ""
        match = _LINK_RE.match(value)
        if not match:
            items.append({"title": value, "href": ""})
            continue
        title = match.group("title").replace(r"\[", "[").replace(r"\]", "]").strip()
        items.append({"title": _single_line(title), "href": match.group("href").strip()})
    return items


def _parse_content_headings(body: str) -> list[dict[str, str]]:
    headings: list[dict[str, str]] = []
    seen_anchors: dict[str, int] = {}
    in_fence = False
    directory_lines = _directory_line_indexes(body)
    for index, line in enumerate((body or "").splitlines()):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or index in directory_lines:
            continue
        if not stripped.startswith(("## ", "### ")):
            continue
        title = _single_line(stripped.lstrip("#").strip())
        if not title or _is_non_content_section_title(title):
            continue
        base = _heading_anchor_id(title)
        count = seen_anchors.get(base, 0) + 1
        seen_anchors[base] = count
        anchor = base if count == 1 else f"{base}-{count}"
        headings.append({"title": title, "anchor": anchor})
    return headings


def _directory_line_indexes(body: str) -> set[int]:
    lines = (body or "").splitlines()
    indexes: set[int] = set()
    in_directory = False
    saw_item = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "## 目录纲要":
            in_directory = True
            saw_item = False
        if not in_directory:
            continue
        if stripped and saw_item and not _is_directory_item(stripped):
            in_directory = False
            continue
        if _is_directory_item(stripped):
            saw_item = True
        indexes.add(index)
    return indexes


def _has_processing_note(body: str) -> bool:
    return any(_is_capture_processing_note_line(line.strip()) for line in (body or "").splitlines())


def _outline_metadata_ready(value: str) -> bool:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, list) and bool(parsed)


def _is_absolute_or_local_url(value: str) -> bool:
    lower = value.strip().lower()
    return lower.startswith(
        (
            "http://",
            "https://",
            "http://127.0.0.1",
            "https://127.0.0.1",
            "http://localhost",
            "https://localhost",
        )
    )

_LINK_RE = re.compile(r"^\[(?P<title>(?:\\.|[^\]])+)\]\((?P<href>[^)]+)\)$")


if __name__ == "__main__":
    raise SystemExit(main())
