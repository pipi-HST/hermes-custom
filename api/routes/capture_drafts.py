"""Capture draft API routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api.routes._bridge_response import bridge_result_response
from api.routes._confirmation import issue_confirmation_token, require_confirmation_token
from db.confirmation_repo import ConfirmationRepo
from db.repo import CaptureDraftRepo

COMMIT_CAPTURE_DRAFT_INTENT = "commit_capture_draft"


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class CreateCaptureDraftRequest(BaseModel):
    content: str = Field(max_length=5_000_000)
    instruction: str = Field(default="", max_length=20_000)
    source: str = Field(default="", max_length=4096)
    source_type: str = Field(default="", max_length=100)


class CreateEditableCaptureDraftRequest(BaseModel):
    content: str = Field(max_length=5_000_000)
    instruction: str = Field(default="", max_length=20_000)
    source: str = Field(default="", max_length=4096)
    source_type: str = Field(default="manual", max_length=100)
    title: str = Field(default="", max_length=500)
    folder_path: str = Field(default="", max_length=1000)
    organize: bool = True
    existing_folders: list[str] = []


class UpdateEditableCaptureDraftRequest(BaseModel):
    content: str = Field(max_length=5_000_000)
    title: str = Field(default="", max_length=500)
    summary: Optional[str] = None
    tags: Optional[list[str]] = None
    folder_path: Optional[str] = None
    folder_suggestion: Optional[dict] = None
    card_suggestion: Optional[dict] = None
    organize: bool = True
    existing_folders: list[str] = []


class ReviseCaptureDraftRequest(BaseModel):
    instruction: str


class CommitCaptureDraftRequest(BaseModel):
    confirmation_token: str = Field(default="", max_length=200)


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

        if _is_local_file_input(body.content):
            raise HTTPException(
                status_code=400,
                detail="local file capture is not allowed through the API",
            )

        result = capture_bridge.capture_content(
            content=body.content,
            instruction=body.instruction,
            source=body.source,
            source_type=body.source_type,
        )
        return bridge_result_response(result)

    @router.post("/capture-drafts/editable")
    def create_editable_capture_draft(body: CreateEditableCaptureDraftRequest):
        from bridge import capture_bridge

        if _is_local_file_input(body.content):
            raise HTTPException(
                status_code=400,
                detail="local file capture is not allowed through the API",
            )

        result = capture_bridge.create_editable_capture_draft(
            content=body.content,
            instruction=body.instruction,
            source=body.source,
            source_type=body.source_type,
            title=body.title,
            folder_path=body.folder_path,
            organize=body.organize,
            existing_folders=body.existing_folders,
        )
        return bridge_result_response(result)

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
            summary=body.summary,
            tags=body.tags,
            folder_path=body.folder_path,
            folder_suggestion=body.folder_suggestion,
            card_suggestion=body.card_suggestion,
            organize=body.organize,
            existing_folders=body.existing_folders,
        )
        return bridge_result_response(result)

    @router.post("/capture-drafts/{draft_id}/revise")
    def revise_capture_draft(draft_id: str, body: ReviseCaptureDraftRequest):
        from bridge import capture_bridge

        result = capture_bridge.revise_capture_draft(
            draft_id=draft_id,
            instruction=body.instruction,
        )
        return bridge_result_response(result)

    @router.post("/capture-drafts/{draft_id}/confirmation")
    def issue_capture_draft_confirmation(draft_id: str):
        draft = draft_repo.get_by_id(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="not found")
        return issue_confirmation_token(
            ConfirmationRepo(draft_repo.conn),
            intent=COMMIT_CAPTURE_DRAFT_INTENT,
            object_id=draft_id,
        )

    @router.post("/capture-drafts/{draft_id}/commit")
    def commit_capture_draft(
        draft_id: str,
        body: CommitCaptureDraftRequest | None = None,
    ):
        from bridge import capture_bridge

        require_confirmation_token(
            ConfirmationRepo(draft_repo.conn),
            intent=COMMIT_CAPTURE_DRAFT_INTENT,
            object_id=draft_id,
            token=body.confirmation_token if body else "",
        )
        result = capture_bridge.commit_capture_draft(
            draft_id=draft_id,
            confirmation_source="api",
            token_verified=True,
        )
        return bridge_result_response(result)

    return router


def _is_local_file_input(content: str) -> bool:
    """Reject local-file inputs without touching the filesystem.

    The old implementation called ``Path(value).is_file()`` which turned this
    check into a file-existence oracle for any API client.  Local paths are
    never valid capture content over HTTP, so reject them by shape only.
    """
    value = (content or "").strip()
    if not value:
        return False
    lowered = value.lower()
    if lowered.startswith("file://"):
        return True
    if "\x00" in value:
        return True
    # Absolute Windows (C:\ or C:/) and POSIX paths, plus drive-relative forms.
    if value.startswith(("/", "\\", "~")):
        return True
    if len(value) >= 3 and value[1] == ":" and value[2] in ("\\", "/") and value[0].isalpha():
        return True
    return False
