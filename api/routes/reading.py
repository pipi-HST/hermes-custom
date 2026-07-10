"""Reading API routes."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.routes._bridge_response import bridge_result_response
from db.reading_repo import ReadingRepo


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class CreateDraftRequest(BaseModel):
    session_id: str
    output_type: str
    instruction: str


class ReviseDraftRequest(BaseModel):
    instruction: str


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_reading_router(reading_repo: ReadingRepo, config: dict) -> APIRouter:
    router = APIRouter()

    # ----- sessions --------------------------------------------------------

    @router.get("/reading/sessions")
    def list_reading_sessions(
        status: str = Query(default="active"),
        limit: int = Query(default=20, le=200),
    ):
        rows = reading_repo.conn.execute(
            "SELECT * FROM reading_sessions WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
        items = [_strip_binary(_deserialize_meta(dict(r))) for r in rows]
        total = len(items)
        return {"items": items, "total": total}

    @router.get("/reading/sessions/{session_id}")
    def get_reading_session(session_id: str):
        session = reading_repo.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="not found")
        session = _deserialize_meta(session)
        turns = [_strip_binary(_deserialize_meta(t)) for t in reading_repo.list_turns(session_id)]
        evidence = [_strip_binary(_deserialize_meta(e)) for e in reading_repo.list_evidence(session_id)]
        return {**_strip_binary(session), "turns": turns, "evidence": evidence}

    # ----- drafts ----------------------------------------------------------

    @router.post("/reading/drafts")
    def create_reading_draft(body: CreateDraftRequest):
        from bridge import reading_bridge  # late import to avoid circular deps

        result = reading_bridge.summarize_reading_session(
            session_id=body.session_id,
            output_type=body.output_type,
            instruction=body.instruction,
        )
        return bridge_result_response(result)

    @router.get("/reading/drafts")
    def list_reading_drafts(
        limit: int = Query(default=20, le=200),
    ):
        rows = reading_repo.conn.execute(
            "SELECT * FROM reading_drafts WHERE status = 'open' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        items = [_strip_binary(_deserialize_meta(dict(r))) for r in rows]
        return {"items": items}

    @router.get("/reading/drafts/{draft_id}")
    def get_reading_draft(draft_id: str):
        draft = reading_repo.get_draft(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="not found")
        return _strip_binary(_deserialize_meta(draft))

    @router.post("/reading/drafts/{draft_id}/revise")
    def revise_reading_draft(draft_id: str, body: ReviseDraftRequest):
        from bridge import answer_bridge  # late import to avoid circular deps

        result = answer_bridge.revise_answer_draft(
            draft_id=draft_id,
            instruction=body.instruction,
        )
        return bridge_result_response(result)

    @router.post("/reading/drafts/{draft_id}/commit")
    def commit_reading_draft(draft_id: str):
        from bridge import reading_bridge  # late import to avoid circular deps

        result = reading_bridge.commit_reading_artifact(draft_id)
        return bridge_result_response(result)

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_binary(d: dict) -> dict:
    """Remove non-JSON-serializable binary fields."""
    return {k: v for k, v in d.items() if not isinstance(v, bytes)}


def _deserialize_meta(d: dict) -> dict:
    """Parse meta_json fields that are stored as JSON strings."""
    out = dict(d)
    for key in ("meta_json", "metadata", "extra"):
        val = out.get(key)
        if isinstance(val, str):
            try:
                out[key] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
    return out
