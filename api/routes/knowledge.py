"""Knowledge card and answer draft API routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from db.reading_repo import ReadingRepo
from db.repo import CaptureRepo


class AnswerDraftRequest(BaseModel):
    hermes_session_id: str = ""
    user_message: str = ""
    assistant_answer: str = ""
    instruction: str = ""
    output_type: str = "auto"
    sources: list[dict[str, Any]] = []
    target_capture_id: str = ""


class ReviseDraftRequest(BaseModel):
    instruction: str = ""


def create_knowledge_router(
    reading_repo: ReadingRepo,
    capture_repo: CaptureRepo,
    vault_path: str = "~/Obsidian/hermes-vault",
) -> APIRouter:
    router = APIRouter()

    @router.post("/knowledge/answer-drafts")
    def save_answer_draft(body: AnswerDraftRequest):
        from bridge import answer_bridge

        result = answer_bridge.save_answer_to_knowledge(
            hermes_session_id=body.hermes_session_id,
            user_message=body.user_message,
            assistant_answer=body.assistant_answer,
            instruction=body.instruction,
            output_type=body.output_type,
            sources=body.sources,
            target_capture_id=body.target_capture_id,
        )
        return result

    @router.get("/knowledge/drafts")
    def list_drafts(limit: int = Query(default=50, le=200)):
        rows = reading_repo.conn.execute(
            "SELECT * FROM reading_drafts WHERE status = 'open' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        items = [_strip_binary(dict(row)) for row in rows]
        return {"items": items}

    @router.get("/knowledge/drafts/{draft_id}")
    def get_draft(draft_id: str):
        draft = reading_repo.get_draft(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="not found")
        return _strip_binary(draft)

    @router.post("/knowledge/drafts/{draft_id}/revise")
    def revise_draft(draft_id: str, body: ReviseDraftRequest):
        from bridge import answer_bridge

        result = answer_bridge.revise_answer_draft(draft_id, body.instruction)
        return result

    @router.post("/knowledge/drafts/{draft_id}/commit")
    def commit_draft(draft_id: str):
        from bridge import reading_bridge

        result = reading_bridge.commit_reading_artifact(draft_id)
        return result

    @router.get("/knowledge/garden-cards")
    def list_garden_cards(limit: int = Query(default=50, le=200)):
        rows = capture_repo.conn.execute(
            "SELECT * FROM garden_cards WHERE status IN ('seed', 'accepted') ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        items = [_strip_binary(dict(row)) for row in rows]
        return {"items": items}

    return router


def _strip_binary(d: dict) -> dict:
    """Remove non-JSON-serializable binary fields."""
    return {k: v for k, v in d.items() if not isinstance(v, bytes)}
