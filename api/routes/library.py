"""Library write endpoints (contract v1.1).

The dashboard's HTTP mode delegates its library writes here: creating/editing
capture drafts, archiving or discarding items and managing folders.  The
request/response shapes are pinned by the runtime ``KnowledgeClient`` — keep the
JSON keys stable.

Business logic stays in the existing bridges/services:
``bridge.capture_bridge`` for drafts, ``PATCH /captures`` semantics for capture
edits, ``bridge.knowledge_write_service`` for Markdown writes and
``bridge.folder_store`` for the folder list.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from api.routes.captures import apply_capture_update
from bridge.folder_store import (
    normalize_folder,
    rename_stored_folder,
    save_folder,
    validate_folder_name,
)
from bridge.library_view import (
    capture_dependencies,
    draft_library_item,
    folder_response,
    get_library_item as get_library_item_view,
    list_library_items as list_library_items_view,
    now_iso,
    rename_folder_everywhere,
)
from db.concurrency import transaction
from db.repo import CaptureDraftRepo, CaptureRepo
from db.reading_repo import ReadingRepo

UNSUPPORTED_KIND_DETAIL = "Unsupported library item kind"


class LibraryItemCreateRequest(BaseModel):
    body: str = ""
    title: str = ""
    source: str = ""
    source_type: str = "manual"
    folder_path: str = "Inbox"
    # Quick manual notes must not block on an LLM rewrite.  Clients that want
    # title/summary/folder/card suggestions opt in explicitly.
    organize: bool = False


class LibraryItemUpdateRequest(BaseModel):
    title: str = ""
    body: str | None = None
    summary: str | None = None
    tags: list[str] | None = None
    folder_path: str | None = None
    folder_suggestion: dict[str, Any] | None = None
    card_suggestion: dict[str, Any] | None = None


class LibraryFolderCreateRequest(BaseModel):
    name: str = ""


class LibraryFolderUpdateRequest(BaseModel):
    name: str = ""


class CaptureDraftUpdateRequest(BaseModel):
    title: str = ""
    draft_text: str | None = None
    summary: str | None = None
    tags: list[str] | None = None
    capture_intent: dict[str, Any] | None = None


def create_library_router(
    capture_repo: CaptureRepo,
    draft_repo: CaptureDraftRepo,
    reading_repo: ReadingRepo,
    vault_path: str = "~/Hermes/knowledge",
    config: object | None = None,
) -> APIRouter:
    router = APIRouter()
    vault = Path(vault_path).expanduser()

    def _context():
        if config is None:
            return None
        from bridge.context import KnowledgeContext

        return KnowledgeContext(config=config, conn=capture_repo.conn, vault_path=vault)

    def _patch_library_draft(item_id: str, body: LibraryItemUpdateRequest) -> dict:
        draft = draft_repo.get_by_id(item_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="Library draft not found")
        updates: dict[str, Any] = {}
        if (body.title or "").strip():
            updates["title"] = body.title.strip()
        if body.body is not None:
            updates["draft_text"] = body.body
            updates["word_count"] = len(body.body or "")
        if body.summary is not None:
            updates["summary"] = body.summary
        if body.tags is not None:
            updates["tags"] = list(body.tags)
        capture_intent = _json_dict(draft.get("capture_intent"))
        intent_changed = False
        if body.folder_path is not None:
            capture_intent["folder_path"] = normalize_folder(body.folder_path)
            intent_changed = True
        if body.folder_suggestion is not None:
            capture_intent["folder_suggestion"] = body.folder_suggestion
            intent_changed = True
        if body.card_suggestion is not None:
            capture_intent["card_suggestion"] = body.card_suggestion
            intent_changed = True
        if intent_changed:
            updates["capture_intent"] = capture_intent
        with transaction(draft_repo.conn):
            updated = draft_repo.update(item_id, **updates) or draft
        return {
            "ok": True,
            "item": draft_library_item(updated, conn=capture_repo.conn, full=True),
        }

    @router.get("/library/items")
    def list_library_items(
        kind: str = Query(default=""),
        folder: str = Query(default=""),
        status: str = Query(default=""),
        search: str = Query(default=""),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ):
        return list_library_items_view(
            capture_repo.conn,
            vault,
            kind=kind,
            folder=folder,
            status=status,
            search=search,
            limit=limit,
            offset=offset,
        )

    @router.get("/library/items/{kind}/{item_id}")
    def get_library_item(kind: str, item_id: str):
        kind = (kind or "").strip()
        item = get_library_item_view(capture_repo.conn, vault, kind, item_id)
        if item is None:
            if kind not in {"capture", "capture_draft"}:
                raise HTTPException(status_code=400, detail=UNSUPPORTED_KIND_DETAIL)
            raise HTTPException(status_code=404, detail="Library item not found")
        return {"ok": True, "item": item}

    @router.post("/library/items")
    def create_library_item(body: LibraryItemCreateRequest):
        from bridge import capture_bridge

        content = (body.body or "").strip()
        title = (body.title or "").strip()
        if not content and not title:
            raise HTTPException(status_code=400, detail="title or body is required")
        result = capture_bridge.create_editable_capture_draft(
            content=content or title,
            source=body.source,
            source_type=body.source_type or "manual",
            instruction="自由编辑后入库到知识库",
            title=title,
            folder_path=normalize_folder(body.folder_path) if body.folder_path else "",
            organize=body.organize,
            context=_context(),
        )
        draft_id = _draft_id_from_result(result)
        item = None
        if draft_id:
            draft = draft_repo.get_by_id(draft_id)
            if draft is not None:
                item = draft_library_item(draft, conn=capture_repo.conn, full=True)
        return {"ok": True, "draft_id": draft_id, "item": item, "result": result}

    @router.patch("/library/items/{kind}/{item_id}")
    def patch_library_item(
        kind: str,
        item_id: str,
        body: LibraryItemUpdateRequest,
        background_tasks: BackgroundTasks,
    ):
        kind = (kind or "").strip()
        if kind == "capture_draft":
            return _patch_library_draft(item_id, body)
        if kind == "capture":
            result = apply_capture_update(
                capture_repo,
                vault,
                config,
                item_id,
                body=body.body or "",
                title=body.title,
                folder_path=body.folder_path,
                background_tasks=background_tasks,
            )
            return {"ok": True, "item": result["item"]}
        raise HTTPException(status_code=400, detail=UNSUPPORTED_KIND_DETAIL)

    @router.delete("/library/items/{kind}/{item_id}")
    def delete_library_item(kind: str, item_id: str):
        kind = (kind or "").strip()
        if kind == "capture_draft":
            from bridge import capture_bridge

            result = capture_bridge.discard_capture_draft(
                draft_id=item_id,
                context=_context(),
            )
            return _with_status(result, "discarded")
        if kind == "capture":
            row = capture_repo.get_by_id(item_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Library capture not found")
            with transaction(capture_repo.conn):
                dependencies = capture_dependencies(capture_repo.conn, item_id)
                capture_repo.conn.execute(
                    "UPDATE captures SET review_state = 'archived', updated_at = ? "
                    "WHERE id = ?",
                    (now_iso(), item_id),
                )
            return {
                "ok": True,
                "status": "archived",
                "id": item_id,
                "dependencies": dependencies,
            }
        raise HTTPException(status_code=400, detail=UNSUPPORTED_KIND_DETAIL)

    @router.get("/library/folders")
    def list_library_folders():
        return folder_response(capture_repo.conn, vault)

    @router.post("/library/folders")
    def create_library_folder(body: LibraryFolderCreateRequest):
        try:
            name = validate_folder_name(body.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        save_folder(vault, name)
        return {"ok": True, "folder": {"path": name, "name": name, "count": 0}}

    @router.patch("/library/folders/{folder_path:path}")
    def rename_library_folder(folder_path: str, body: LibraryFolderUpdateRequest):
        old_folder = normalize_folder(folder_path)
        try:
            new_folder = validate_folder_name(body.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if old_folder == "Inbox":
            raise HTTPException(status_code=400, detail="Inbox cannot be renamed")
        if old_folder != new_folder:
            rename_folder_everywhere(capture_repo.conn, vault, old_folder, new_folder)
            rename_stored_folder(vault, old_folder, new_folder)
        return {
            "ok": True,
            "folders": folder_response(capture_repo.conn, vault)["folders"],
        }

    @router.patch("/capture-drafts/{draft_id}")
    def update_capture_draft(draft_id: str, body: CaptureDraftUpdateRequest):
        draft = draft_repo.get_by_id(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="Draft not found")
        updates: dict[str, Any] = {}
        if (body.title or "").strip():
            updates["title"] = body.title.strip()
        if body.draft_text is not None:
            updates["draft_text"] = body.draft_text
            updates["word_count"] = len(body.draft_text or "")
        if body.summary is not None:
            updates["summary"] = body.summary
        if body.tags is not None:
            updates["tags"] = list(body.tags)
        if body.capture_intent is not None:
            updates["capture_intent"] = {
                **_json_dict(draft.get("capture_intent")),
                **body.capture_intent,
            }
        with transaction(draft_repo.conn):
            updated = draft_repo.update(draft_id, **updates) or draft
        return {
            "ok": True,
            "item": draft_library_item(updated, conn=capture_repo.conn, full=True),
        }

    @router.delete("/capture-drafts/{draft_id}")
    def discard_capture_draft(draft_id: str):
        from bridge import capture_bridge

        result = capture_bridge.discard_capture_draft(
            draft_id=draft_id,
            context=_context(),
        )
        return _with_status(result, "abandoned")

    @router.delete("/reading/drafts/{draft_id}")
    def delete_reading_draft(draft_id: str):
        draft = reading_repo.get_draft(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="not found")
        with transaction(reading_repo.conn):
            reading_repo.update_draft(draft_id, status="deleted")
        return {"ok": True, "status": "deleted"}

    return router


def _draft_id_from_result(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    draft_id = str(meta.get("draft_id") or result.get("draft_id") or "").strip()
    if draft_id:
        return draft_id
    for artifact in result.get("durable_artifacts") or []:
        if isinstance(artifact, dict) and artifact.get("type") == "capture_draft":
            return str(artifact.get("id") or "").strip()
    return ""


def _with_status(result: Any, status: str) -> Any:
    """Report the post-action status while preserving bridge error envelopes."""
    if isinstance(result, dict) and result.get("status") == "success":
        return {**result, "status": status}
    return result


def _json_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
