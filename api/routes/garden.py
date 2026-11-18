"""Garden / Knowledge Card endpoints (contract v2 confirmation).

HTTP mode delegates card synthesis and card reads to this service.  Response
shapes mirror the runtime's local handlers so the dashboard consumes the same
fields in both modes.

Synthesis only creates a reviewable draft.  A card is written only after the
caller requests ``POST /garden/drafts/{draft_id}/confirmation`` and presents
that single-use token to ``POST /garden/drafts/{draft_id}/commit``; there is no
``auto_commit`` shortcut.

The card view reads ``garden_cards`` rows and expands ``payload_json`` into the
core fields (``normalize_garden_payload`` keeps legacy rows compatible).  The
card's ``capture_ids`` are only references — deleting a card never deletes or
mutates the captures it points at; graph edges that point at the card are
removed to avoid dangling references.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agents.garden_agent import get_garden_draft, is_open_garden_draft
from agents.garden_card_payload import (
    normalize_garden_payload,
    parse_payload_json,
    payload_from_row,
    project_legacy_fields,
)
from api.routes._confirmation import issue_confirmation_token, require_confirmation_token
from bridge.capture_view import read_capture_note
from bridge.context import KnowledgeContext
from db.concurrency import transaction
from db.confirmation_repo import ConfirmationRepo
from db.repo import CaptureRepo

CARD_KINDS = ("viewpoint", "method", "fact", "risk")
COMMIT_GARDEN_DRAFT_INTENT = "commit_garden_draft"


class GardenDraftRequest(BaseModel):
    capture_ids: list[str] = []
    title: str = ""
    card_kind: str = ""
    instruction: str = ""
    # Synthesis never commits.  Older clients may still send ``auto_commit``;
    # Pydantic ignores the unknown field and the response stays a draft.  The
    # caller then requests a confirmation token for the returned draft id and
    # commits that draft explicitly.


class GardenDraftCommitRequest(BaseModel):
    confirmation_token: str = Field(default="", max_length=200)


def create_garden_router(
    capture_repo: CaptureRepo,
    vault_path: str = "~/Hermes/knowledge",
    config: object | None = None,
) -> APIRouter:
    router = APIRouter()
    vault = Path(vault_path).expanduser()

    def _context():
        return _draft_context() if config is not None else None

    def _draft_context() -> KnowledgeContext:
        # Draft lifecycle operations must use the same database as the router,
        # even when no model configuration is needed or was passed in.
        return KnowledgeContext(config=config, conn=capture_repo.conn, vault_path=vault)

    @router.post("/garden/drafts")
    def create_garden_draft(body: GardenDraftRequest):
        from bridge import garden_bridge

        # Always synthesize only.  This endpoint never writes a card: the
        # caller must confirm the returned draft id via
        # POST /api/garden/drafts/{draft_id}/confirmation and then commit it
        # with that token.  A single request can therefore never bypass the
        # user-confirmation rule.
        context = _context()
        result = garden_bridge.synthesize_garden(
            capture_ids=list(body.capture_ids),
            title=body.title,
            card_kind=body.card_kind,
            instruction=body.instruction,
            context=context,
        )
        return result

    @router.post("/garden/drafts/preview")
    def preview_garden_draft(body: GardenDraftRequest):
        return create_garden_draft(body)

    @router.get("/garden/drafts")
    def list_garden_drafts(
        limit: int = Query(default=50, ge=1, le=200),
        status: str = Query(default="draft_ready"),
    ):
        status_filter = (status or "").strip()
        if status_filter:
            rows = capture_repo.conn.execute(
                """SELECT * FROM garden_drafts
                   WHERE status = ?
                   ORDER BY datetime(replace(substr(updated_at, 1, 19), 'T', ' ')) DESC,
                            id DESC
                   LIMIT ?""",
                (status_filter, limit),
            ).fetchall()
        else:
            rows = capture_repo.conn.execute(
                """SELECT * FROM garden_drafts
                   ORDER BY datetime(replace(substr(updated_at, 1, 19), 'T', ' ')) DESC,
                            id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["one_liner"] = payload_from_row(item)["claim"]
            items.append(item)
        return {"items": items, "total": len(items)}

    @router.post("/garden/drafts/{draft_id}/confirmation")
    def issue_garden_draft_confirmation(draft_id: str):
        with transaction(capture_repo.conn):
            draft = get_garden_draft(capture_repo, draft_id)
            if draft is None:
                raise HTTPException(status_code=404, detail="garden draft not found")
            if not is_open_garden_draft(draft):
                raise HTTPException(status_code=409, detail="garden draft is no longer open")
            return issue_confirmation_token(
                ConfirmationRepo(capture_repo.conn),
                intent=COMMIT_GARDEN_DRAFT_INTENT,
                object_id=draft_id,
                object_version=draft["draft_version"],
            )

    @router.post("/garden/drafts/{draft_id}/commit")
    def commit_garden_draft(
        draft_id: str,
        body: GardenDraftCommitRequest | None = None,
    ):
        from bridge import garden_bridge

        # The token is the only accepted proof of confirmation.  There is no
        # server-side "the HTTP request already means yes" shortcut. Consume
        # the token in the same transaction that saves the confirmed version.
        draft = get_garden_draft(capture_repo, draft_id)
        version = draft["draft_version"] if draft is not None else ""
        token = body.confirmation_token if body else ""

        def consume_confirmation():
            require_confirmation_token(
                ConfirmationRepo(capture_repo.conn),
                intent=COMMIT_GARDEN_DRAFT_INTENT,
                object_id=draft_id,
                token=token,
                object_version=version,
            )

        if not token.strip() or not draft or not is_open_garden_draft(draft):
            # No token for a closed/missing version should have been issued.
            raise HTTPException(
                status_code=403,
                detail={"code": "confirmation_required", "message": "请确认当前待保存草稿。"},
            )
        result = garden_bridge.commit_garden_draft(
            draft_id=draft_id,
            confirmation_source="api",
            token_verified=True,
            context=_draft_context(),
            expected_version=version,
            before_commit=consume_confirmation,
        )
        if (result.get("meta") or {}).get("draft_state_conflict"):
            raise HTTPException(
                status_code=409,
                detail="garden draft changed or is no longer open; please confirm again",
            )
        return result

    @router.delete("/garden/drafts/{draft_id}")
    def discard_garden_draft(draft_id: str):
        from bridge import garden_bridge

        discarded = garden_bridge.discard_garden_draft(draft_id, context=_draft_context())
        if discarded is None:
            raise HTTPException(status_code=404, detail="garden draft not found")
        if not discarded:
            raise HTTPException(status_code=409, detail="garden draft is no longer open")
        return {"ok": True, "draft_id": draft_id, "status": "discarded"}

    @router.get("/garden/cards")
    def list_garden_cards(
        limit: int = Query(default=100, ge=1, le=300),
        status: str = Query(default=""),
    ):
        status_filter = (status or "").strip()
        if status_filter:
            rows = capture_repo.conn.execute(
                """SELECT * FROM garden_cards
                   WHERE status = ?
                   ORDER BY datetime(replace(substr(updated_at, 1, 19), 'T', ' ')) DESC,
                            id DESC
                   LIMIT ?""",
                (status_filter, limit),
            ).fetchall()
        else:
            rows = capture_repo.conn.execute(
                """SELECT * FROM garden_cards
                   WHERE COALESCE(status, '') != 'archived'
                   ORDER BY datetime(replace(substr(updated_at, 1, 19), 'T', ' ')) DESC,
                            id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        items = [_card_view(capture_repo.conn, vault, row) for row in rows]
        return {"items": items, "total": len(items)}

    @router.delete("/garden/cards/{card_id}")
    def delete_garden_card(card_id: str):
        from bridge import garden_bridge

        # Same semantics as the dashboard's local DELETE /api/knowledge/cards:
        # archive the card (keep the row and its history), never hard-delete.
        return garden_bridge.archive_garden_card(card_id=card_id, context=_context())

    return router


def _card_view(conn, vault_path: Path, row) -> dict:
    """Library-style card view (mirrors the runtime's ``GET /api/knowledge/cards``)."""
    data = dict(row)
    raw_payload = parse_payload_json(str(data.get("payload_json") or "{}"))
    payload = normalize_garden_payload(raw_payload, legacy=data)
    legacy = project_legacy_fields(payload)
    capture_ids = payload.get("source_ids") or _json_list(data.get("capture_ids"))
    capture_ids = [str(item).strip() for item in capture_ids if str(item).strip()]
    summary = str(payload.get("claim") or "").strip()
    kind = str(payload.get("card_kind") or data.get("card_kind") or "viewpoint").strip()
    if kind not in CARD_KINDS:
        kind = "viewpoint"
    return {
        "id": str(data.get("id") or ""),
        "title": payload.get("title") or data.get("title") or "未命名卡片",
        "kind": kind,
        "summary": summary,
        "status": str(data.get("status") or ""),
        "updated_at": str(data.get("updated_at") or ""),
        "created_at": str(data.get("created_at") or ""),
        "file_path": "",
        "markdown": "",
        "source_theme": str(
            data.get("source_theme") or raw_payload.get("source_theme") or ""
        ),
        "content_markdown": str(payload.get("content_markdown") or ""),
        "core_arguments": legacy["core_arguments"],
        "knowledge_links": legacy["knowledge_links"],
        "key_materials": legacy["key_materials"],
        "my_view": legacy["my_view"],
        "sources": [
            _source_for_capture(conn, vault_path, capture_id)
            for capture_id in capture_ids
        ],
        "source_count": len(capture_ids),
        "citation_count": int(data.get("citation_count") or 0),
        "last_cited_at": str(data.get("last_cited_at") or ""),
    }


def _source_for_capture(conn, vault_path: Path, capture_id: str) -> dict:
    row = conn.execute(
        "SELECT id, file_path FROM captures WHERE id = ?", (capture_id,)
    ).fetchone()
    if row is None and len(capture_id) >= 6:
        rows = conn.execute(
            "SELECT id, file_path FROM captures WHERE id LIKE ? "
            "ORDER BY updated_at DESC LIMIT 2",
            (f"{capture_id}%",),
        ).fetchall()
        if len(rows) == 1:
            row = rows[0]
    if row is None:
        return {
            "id": capture_id,
            "title": "未找到来源材料",
            "file_path": "",
            "summary": "",
        }
    file_path = str(row["file_path"] or "")
    frontmatter, _body = read_capture_note(vault_path, {"file_path": file_path})
    title = (
        str(frontmatter.get("title") or "").strip()
        or Path(file_path).stem
        or str(row["id"])
    )
    return {
        "id": str(row["id"]),
        "title": title,
        "file_path": file_path,
        "summary": str(frontmatter.get("story") or "").strip(),
    }


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []
