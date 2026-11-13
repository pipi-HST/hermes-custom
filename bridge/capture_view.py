"""Shared capture view hydration.

The HTTP capture routes and the RAG bridge both render a capture row together
with its Markdown note.  One implementation keeps those views from drifting
apart (the API used ``summary`` while RAG used ``story`` for the same
frontmatter field).
"""
from __future__ import annotations

from pathlib import Path

from agents.markdown_writer import read_note


def read_capture_note(vault_path: str | Path, row: dict) -> tuple[dict, str]:
    """Return ``(frontmatter, body)`` for the note the capture row points at."""
    file_path = row.get("file_path") or row.get("raw_path") or ""
    if not file_path:
        return {}, ""
    note = read_note(Path(vault_path).expanduser(), file_path)
    if not note:
        return {}, ""
    return note


def build_capture_view(row: dict, frontmatter: dict, body: str) -> dict:
    """Merge one capture DB row with the note's frontmatter and body."""
    tags = frontmatter.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]

    return {
        **row,
        "title": frontmatter.get("title", ""),
        "summary": frontmatter.get("story", ""),
        "tags": tags,
        "source": frontmatter.get("source", ""),
        "source_url": frontmatter.get("source", ""),
        "source_type": frontmatter.get("source_type", ""),
        "status": frontmatter.get("status", "captured" if row.get("file_path") else "raw"),
        "folder_path": frontmatter.get("folder_path") or frontmatter.get("folder") or "Inbox",
        "word_count": frontmatter.get("word_count", len(body)),
        "body": body,
    }


def hydrate_capture(vault_path: str | Path, row: dict) -> dict:
    """API-shaped capture view used by list/detail/search responses."""
    frontmatter, body = read_capture_note(vault_path, row)
    return build_capture_view(row, frontmatter, body)
