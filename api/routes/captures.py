"""Captures API routes."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel
import yaml

from agents.capture_agent import CaptureConfig
from agents.embedder import Embedder
from agents.library_agent import LibraryAgent
from agents.markdown_writer import read_note
from agents.outliner import Outliner
from bridge.capture_view import hydrate_capture as _hydrate_capture
from bridge.knowledge_write_service import KnowledgeWriteService, MarkdownWriteRequest
from db.repo import CaptureRepo

CAPTURE_SCAN_PAGE_SIZE = 200
CAPTURE_MAX_SCAN_ROWS = 20_000


class UpdateCaptureRequest(BaseModel):
    body: str = ""
    title: str = ""
    folder_path: Optional[str] = None


class ReviseCaptureNoteRequest(BaseModel):
    instruction: str = ""


def create_capture_router(
    repo: CaptureRepo,
    vault_path: str = "~/Hermes/knowledge",
    config: CaptureConfig | dict[str, Any] | None = None,
) -> APIRouter:
    router = APIRouter()
    vault = Path(vault_path).expanduser()

    @router.get("/captures")
    def list_captures(
        limit: int = Query(default=50, le=200),
        offset: int = Query(default=0, ge=0),
        tag: str = Query(default=""),
        search: str = Query(default=""),
        folder: str = Query(default=""),
    ):
        has_filters = bool(tag or search or folder)
        needed = offset + limit
        matched: list[dict] = []
        scanned = 0
        while len(matched) < needed and scanned < CAPTURE_MAX_SCAN_ROWS:
            rows = repo.list_all(limit=CAPTURE_SCAN_PAGE_SIZE, offset=scanned)
            if not rows:
                break
            for row in rows:
                item = _hydrate_capture(vault, row)
                if _matches_filters(item, tag=tag, search=search, folder=folder):
                    matched.append(item)
                    if len(matched) >= needed:
                        break
            scanned += len(rows)
            if len(rows) < CAPTURE_SCAN_PAGE_SIZE:
                break
        items = matched[offset:offset + limit]
        if has_filters:
            total = len(matched)
            # The scan stops once the requested page is full or at the row cap,
            # so a filtered total may be an undercount of all matching rows.
            total_is_lower_bound = (
                len(matched) >= needed or scanned >= CAPTURE_MAX_SCAN_ROWS
            )
        else:
            total = repo.count()
            total_is_lower_bound = False
        return {
            "items": [_strip_binary(i) for i in items],
            "total": total,
            "total_is_lower_bound": total_is_lower_bound,
        }

    @router.get("/captures/{capture_id}")
    def get_capture(capture_id: str):
        item = repo.get_by_id(capture_id)
        if item is None:
            raise HTTPException(status_code=404, detail="not found")
        return _strip_binary(_hydrate_capture(vault, item))

    @router.patch("/captures/{capture_id}")
    def update_capture(
        capture_id: str,
        body: UpdateCaptureRequest,
        background_tasks: BackgroundTasks,
    ):
        return apply_capture_update(
            repo,
            vault,
            config,
            capture_id,
            body=body.body,
            title=body.title,
            folder_path=body.folder_path,
            background_tasks=background_tasks,
        )

    @router.post("/captures/{capture_id}/revise")
    async def revise_capture_note(
        capture_id: str, body: ReviseCaptureNoteRequest
    ):
        instruction = (body.instruction or "").strip()
        if not instruction:
            raise HTTPException(status_code=400, detail="instruction cannot be empty")
        item = repo.get_by_id(capture_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Capture not found")
        file_path = str(item.get("file_path") or "")
        if not file_path:
            raise HTTPException(status_code=400, detail="capture has no editable note")
        note = read_note(vault, file_path)
        if note is None:
            raise HTTPException(status_code=404, detail="capture note file not found")
        frontmatter, current_body = note
        raw_note = read_note(vault, str(item.get("raw_path") or ""))
        _raw_frontmatter, raw_body = raw_note if raw_note is not None else ({}, "")
        title = str(frontmatter.get("title") or "").strip()
        prompt = _capture_revision_prompt(
            instruction=instruction,
            title=title,
            current_body=current_body,
            raw_body=raw_body,
        )
        from agents.capture_agent import CaptureAgent

        if isinstance(config, CaptureConfig):
            cfg = config
        elif isinstance(config, dict):
            cfg = CaptureConfig(
                llm_api_key=str(config.get("llm_api_key", "")),
                llm_base_url=str(config.get("llm_base_url", "")),
                qwen_api_key=str(config.get("qwen_api_key", "")),
            )
        else:
            cfg = CaptureConfig(
                llm_api_key=str(getattr(config, "llm_api_key", "")),
                llm_base_url=str(getattr(config, "llm_base_url", "")),
                qwen_api_key=str(getattr(config, "qwen_api_key", "")),
            )
        agent = CaptureAgent(cfg)
        llm_result = await asyncio.to_thread(agent._call_llm, prompt)
        revised_body = str(getattr(llm_result, "text", llm_result) or "").strip()
        if getattr(llm_result, "incomplete", False):
            raise HTTPException(status_code=502, detail="AI revision output was incomplete")
        if not revised_body:
            raise HTTPException(status_code=502, detail="AI revision output was empty")
        summary_result = await asyncio.to_thread(
            agent.summarizer.summarize, revised_body, []
        )
        return {
            "ok": True,
            "capture_id": capture_id,
            "title": str(summary_result.get("title") or title or "").strip(),
            "body": revised_body,
            "summary": str(summary_result.get("summary") or "").strip(),
            "tags": summary_result.get("tags", []),
            "source": "ai_revision",
        }

    return router


def _capture_revision_prompt(
    *, instruction: str, title: str, current_body: str, raw_body: str
) -> str:
    raw_section = raw_body.strip() or "（没有单独 raw 来源材料；以当前笔记为准。）"
    return f"""你是 Hermes Knowledge 的入库笔记修订助手。

用户要修改的笔记标题：{title or "未命名"}

用户修改指令：
{instruction}

硬性要求：
1. 只输出修订后的 Markdown 正文，不要输出解释、前言或代码块。
2. 除非用户明确要求总结、压缩、删除或只保留部分内容，否则不得删减信息、不得改变原文观点、不得丢失例子。
3. 可以改善标题层级、段落、列表、空行和表达清晰度。
4. 当前笔记是要被修订的正文；原始来源材料只用于校验和补回遗漏信息。

当前笔记正文：
{current_body}

原始来源材料：
{raw_section}
"""


def apply_capture_update(
    repo: CaptureRepo,
    vault: Path,
    config: CaptureConfig | dict[str, Any] | None,
    capture_id: str,
    *,
    body: str = "",
    title: str = "",
    folder_path: str | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> dict:
    """Shared implementation of ``PATCH /captures/{id}`` and library updates."""
    item = repo.get_by_id(capture_id)
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    file_path = str(item.get("file_path") or "")
    if not file_path:
        raise HTTPException(status_code=400, detail="capture has no editable note")
    note = read_note(vault, file_path)
    if note is None:
        raise HTTPException(status_code=404, detail="资料笔记文件不存在")

    frontmatter, current_body = note
    updated_body = (body or current_body or "").strip()
    if not updated_body:
        raise HTTPException(status_code=400, detail="body cannot be empty")
    if (title or "").strip():
        frontmatter["title"] = title.strip()
        frontmatter["title_source"] = "user"
    if folder_path is not None:
        frontmatter["folder_path"] = folder_path.strip() or "Inbox"
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
    # Library maintenance calls an LLM with a 120s timeout. Keep it off the
    # request path so it cannot block every other API call.
    if background_tasks is not None:
        background_tasks.add_task(
            _maintain_capture_after_edit,
            repo,
            vault,
            capture_id,
            config,
        )
    return {
        "capture_id": capture_id,
        "file_path": file_path,
        "backup_path": write_result.backup_path,
        "library_postprocess": {"status": "queued"},
        "item": _strip_binary(_hydrate_capture(vault, repo.get_by_id(capture_id) or item)),
    }


def _strip_binary(d: dict) -> dict:
    """移除不可 JSON 序列化的二进制字段。"""
    return {k: v for k, v in d.items() if not isinstance(v, bytes)}


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
        logging.getLogger(__name__).exception("post-edit library maintenance failed")
        return {"error": str(exc), "error_type": type(exc).__name__}


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
