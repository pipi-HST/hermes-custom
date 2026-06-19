"""Capture draft API routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from db.repo import CaptureDraftRepo


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class CreateCaptureDraftRequest(BaseModel):
    content: str
    instruction: str = ""
    source: str = ""
    source_type: str = ""


class CreateEditableCaptureDraftRequest(BaseModel):
    content: str
    instruction: str = ""
    source: str = ""
    source_type: str = "manual"
    title: str = ""


class UpdateEditableCaptureDraftRequest(BaseModel):
    content: str
    title: str = ""


class ReviseCaptureDraftRequest(BaseModel):
    instruction: str


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_capture_draft_router(draft_repo: CaptureDraftRepo) -> APIRouter:
    router = APIRouter()

    @router.get("/capture-drafts")
    def list_capture_drafts(limit: int = Query(default=50, le=200)):
        items = draft_repo.list_open(limit=limit)
        return {"items": items, "total": len(items)}

    @router.get("/capture-drafts/{draft_id}")
    def get_capture_draft(draft_id: str):
        draft = draft_repo.get_by_id(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="not found")
        return draft

    @router.post("/capture-drafts")
    def create_capture_draft(body: CreateCaptureDraftRequest):
        from bridge import capture_bridge

        result = capture_bridge.capture_content(
            content=body.content,
            instruction=body.instruction,
            source=body.source,
            source_type=body.source_type,
        )
        return result

    @router.post("/capture-drafts/editable")
    def create_editable_capture_draft(body: CreateEditableCaptureDraftRequest):
        from bridge import capture_bridge

        result = capture_bridge.create_editable_capture_draft(
            content=body.content,
            instruction=body.instruction,
            source=body.source,
            source_type=body.source_type,
            title=body.title,
        )
        return result

    @router.patch("/capture-drafts/{draft_id}/editable")
    def update_editable_capture_draft(
        draft_id: str,
        body: UpdateEditableCaptureDraftRequest,
    ):
        from bridge import capture_bridge

        result = capture_bridge.update_editable_capture_draft(
            draft_id=draft_id,
            content=body.content,
            title=body.title,
        )
        return result

    @router.post("/capture-drafts/{draft_id}/revise")
    def revise_capture_draft(draft_id: str, body: ReviseCaptureDraftRequest):
        from bridge import capture_bridge

        result = capture_bridge.revise_capture_draft(
            draft_id=draft_id,
            instruction=body.instruction,
        )
        return result

    @router.post("/capture-drafts/{draft_id}/commit")
    def commit_capture_draft(draft_id: str):
        from bridge import capture_bridge

        result = capture_bridge.commit_capture_draft(draft_id=draft_id)
        return result

    return router
