"""Captures API routes."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from agents.markdown_writer import read_note
from db.repo import CaptureRepo


def create_capture_router(repo: CaptureRepo, vault_path: str = "~/Obsidian/hermes-vault") -> APIRouter:
    router = APIRouter()
    vault = Path(vault_path).expanduser()

    @router.get("/captures")
    def list_captures(
        limit: int = Query(default=50, le=200),
        offset: int = Query(default=0),
        tag: str = Query(default=""),
        search: str = Query(default=""),
        folder: str = Query(default=""),
    ):
        filtered = [
            item for item in (
                _hydrate_capture(vault, row)
                for row in repo.list_all(limit=10000, offset=0)
            )
            if _matches_filters(item, tag=tag, search=search, folder=folder)
        ]
        items = filtered[offset:offset + limit]
        return {"items": [_strip_binary(i) for i in items], "total": len(items)}

    @router.get("/captures/{capture_id}")
    def get_capture(capture_id: str):
        item = repo.get_by_id(capture_id)
        if item is None:
            raise HTTPException(status_code=404, detail="not found")
        return _strip_binary(_hydrate_capture(vault, item))

    return router


def _strip_binary(d: dict) -> dict:
    """移除不可 JSON 序列化的二进制字段。"""
    return {k: v for k, v in d.items() if not isinstance(v, bytes)}


def _hydrate_capture(vault_path: Path, row: dict) -> dict:
    file_path = row.get("file_path") or row.get("raw_path") or ""
    frontmatter: dict = {}
    body = ""
    if file_path:
        note = read_note(vault_path, file_path)
        if note:
            frontmatter, body = note

    tags = frontmatter.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]

    hydrated = {
        **row,
        "title": frontmatter.get("title", ""),
        "summary": frontmatter.get("story", ""),
        "tags": tags,
        "source": frontmatter.get("source", ""),
        "source_url": frontmatter.get("source", ""),
        "source_type": frontmatter.get("source_type", ""),
        "status": frontmatter.get("status", "captured" if row.get("file_path") else "raw"),
        "word_count": frontmatter.get("word_count", len(body)),
        "body": body,
    }
    return hydrated


def _matches_filters(item: dict, *, tag: str, search: str, folder: str) -> bool:
    if tag and tag not in item.get("tags", []):
        return False
    if folder and item.get("folder", "") != folder:
        return False
    if search:
        haystack = " ".join(
            str(item.get(key, ""))
            for key in ("title", "summary", "body", "source")
        ).lower()
        if search.lower() not in haystack:
            return False
    return True
