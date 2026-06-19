"""Captures API routes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
import yaml

from agents.capture_agent import CaptureConfig
from agents.embedder import Embedder
from agents.library_agent import LibraryAgent
from agents.markdown_writer import read_note
from agents.outliner import Outliner
from bridge.knowledge_write_service import KnowledgeWriteService, MarkdownWriteRequest
from db.repo import CaptureRepo


class UpdateCaptureRequest(BaseModel):
    body: str = ""
    title: str = ""
    folder_path: str | None = None


def create_capture_router(
    repo: CaptureRepo,
    vault_path: str = "~/Obsidian/hermes-vault",
    config: CaptureConfig | dict[str, Any] | None = None,
) -> APIRouter:
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

    @router.patch("/captures/{capture_id}")
    def update_capture(capture_id: str, body: UpdateCaptureRequest):
        item = repo.get_by_id(capture_id)
        if item is None:
            raise HTTPException(status_code=404, detail="not found")
        file_path = str(item.get("file_path") or "")
        if not file_path:
            raise HTTPException(status_code=400, detail="capture has no editable note")
        note = read_note(vault, file_path)
        if note is None:
            raise HTTPException(status_code=404, detail="capture note file not found")

        frontmatter, current_body = note
        updated_body = (body.body or current_body or "").strip()
        if not updated_body:
            raise HTTPException(status_code=400, detail="body cannot be empty")
        if body.title.strip():
            frontmatter["title"] = body.title.strip()
            frontmatter["title_source"] = "user"
        if body.folder_path is not None:
            frontmatter["folder_path"] = body.folder_path.strip() or "Inbox"
        frontmatter["word_count"] = len(updated_body)

        write_result = KnowledgeWriteService(vault).write_markdown_artifact(
            MarkdownWriteRequest(
                kind="capture_note",
                relative_path=file_path,
                content=_render_markdown(frontmatter, updated_body),
                db_object_id=capture_id,
                db_table="captures",
                requires_backup=True,
            )
        )
        repo.log_change(
            "capture",
            capture_id,
            "body",
            old_value="",
            new_value="updated via capture API",
            source="api",
        )
        library_report = _maintain_capture_after_edit(
            repo,
            vault,
            capture_id,
            config,
        )
        return {
            "capture_id": capture_id,
            "file_path": file_path,
            "backup_path": write_result.backup_path,
            "library_postprocess": library_report,
            "item": _strip_binary(_hydrate_capture(vault, repo.get_by_id(capture_id) or item)),
        }

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
        "folder_path": frontmatter.get("folder_path") or frontmatter.get("folder") or "Inbox",
        "word_count": frontmatter.get("word_count", len(body)),
        "body": body,
    }
    return hydrated


def _matches_filters(item: dict, *, tag: str, search: str, folder: str) -> bool:
    if tag and tag not in item.get("tags", []):
        return False
    if folder and (item.get("folder_path") or item.get("folder", "")) != folder:
        return False
    if search:
        haystack = " ".join(
            str(item.get(key, ""))
            for key in ("title", "summary", "body", "source")
        ).lower()
        if search.lower() not in haystack:
            return False
    return True


def _render_markdown(frontmatter: dict, body: str) -> str:
    return (
        "---\n"
        + yaml.dump(frontmatter, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + body
    )


def _maintain_capture_after_edit(
    repo: CaptureRepo,
    vault: Path,
    capture_id: str,
    config: CaptureConfig | dict[str, Any] | None,
) -> dict:
    cfg = _capture_route_config(config)
    try:
        outliner = Outliner(
            api_key=cfg.get("llm_api_key", ""),
            model="deepseek-v4-flash",
            base_url=cfg.get("llm_base_url") or "https://api.deepseek.com",
        )
        embedder = (
            Embedder(api_key=cfg.get("qwen_api_key", ""))
            if cfg.get("qwen_api_key")
            else None
        )
        return LibraryAgent(
            vault,
            outliner=outliner,
            db_conn=repo.conn,
            embedder=embedder,
        ).maintain_capture(capture_id, apply=True)
    except Exception as exc:  # pragma: no cover - defensive API degradation
        return {"error": str(exc)}


def _capture_route_config(config: CaptureConfig | dict[str, Any] | None) -> dict[str, str]:
    if config is None:
        return {}
    if isinstance(config, dict):
        return {str(key): str(value or "") for key, value in config.items()}
    return {
        "llm_api_key": config.llm_api_key,
        "llm_base_url": config.llm_base_url,
        "qwen_api_key": config.qwen_api_key,
    }
