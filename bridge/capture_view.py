"""Shared capture view hydration.

The HTTP capture routes and the RAG bridge both render a capture row together
with its Markdown note.  One implementation keeps those views from drifting
apart (the API used ``summary`` while RAG used ``story`` for the same
frontmatter field).
"""
from __future__ import annotations

from pathlib import Path

from agents.markdown_writer import note_revision, read_note_snapshot


def read_capture_note(vault_path: str | Path, row: dict) -> tuple[dict, str]:
    """Return ``(frontmatter, body)`` for the note the capture row points at."""
    snapshot = read_capture_note_snapshot(vault_path, row)
    return snapshot[:2] if snapshot is not None else ({}, "")


def read_capture_note_snapshot(vault_path: str | Path, row: dict) -> tuple[dict, str, str] | None:
    file_path = row.get("file_path") or row.get("raw_path") or ""
    if not file_path:
        return None
    return read_note_snapshot(Path(vault_path), file_path)


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


def hydrate_capture(vault_path: str | Path, row: dict, *, include_revision: bool = False) -> dict:
    """API-shaped capture view used by list/detail/search responses."""
    snapshot = read_capture_note_snapshot(vault_path, row)
    frontmatter, body, raw_text = snapshot if snapshot is not None else ({}, "", "")
    view = build_capture_view(row, frontmatter, body)
    if include_revision and snapshot is not None and row.get("file_path"):
        view["note_revision"] = note_revision(raw_text)
    return view
