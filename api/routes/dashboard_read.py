"""Dashboard-shaped read endpoints.

The Runtime dashboard renders drafts, card detail, dependencies and object
timelines from a few fixed JSON shapes.  Those routes used to open the
Knowledge SQLite directly even in HTTP mode, which breaks on a split host.
They live here so the service owns the shape and the Runtime can delegate.

Paths intentionally mirror the Runtime's ``/api/knowledge/...`` routes.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.routes.garden import _card_view
from bridge.library_view import capture_dependencies

_ACTIVE_AGENTS = {
    "reading_draft": {"reading", "garden"},
    "capture_draft": {"capture", "garden"},
    "capture": {"capture", "garden"},
    "garden_draft": {"garden"},
    "garden_card": {"garden"},
}


def _json_value(raw: str, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except (TypeError, ValueError):
        return fallback


def _clip(text: str, limit: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _capture_draft_view(row: sqlite3.Row, *, full: bool = False) -> dict:
    data = dict(row)
    intent = _json_value(str(data.get("capture_intent") or "{}"), {})
    draft_text = str(data.get("draft_text") or "")
    raw_text = str(data.get("raw_text") or "")
    body = draft_text or raw_text
    view = {
        "id": data.get("id", ""),
        "title": data.get("title", "") or "未命名",
        "status": data.get("status", ""),
        "source": data.get("source", ""),
        "source_type": data.get("source_type", ""),
        "word_count": data.get("word_count", 0),
        "instruction": data.get("instruction", ""),
        "summary": data.get("summary", ""),
        "tags": _json_value(str(data.get("tags") or "[]"), []),
        "capture_intent": intent,
        "user_intent": intent.get("material_intent")
        or ("full_text" if intent.get("output_shape") == ["full_text"] else ""),
        "committed_capture_id": data.get("committed_capture_id", ""),
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", ""),
        "draft_preview": _clip(body, 420),
        "raw_preview": _clip(raw_text, 260),
    }
    if full:
        view["draft_text"] = draft_text
        view["raw_text"] = raw_text
    return view


def _agent_runs_for_object(
    conn: sqlite3.Connection, object_type: str, object_id: str, limit: int
) -> tuple[list[sqlite3.Row], list[str]]:
    """Rows from agent_runs touching the object, newest first."""
    related = [object_id]
    if object_type == "reading_draft":
        row = conn.execute(
            "SELECT target_capture_id FROM reading_drafts WHERE id = ?",
            (object_id,),
        ).fetchone()
        if row and str(row["target_capture_id"] or "").strip():
            related.append(str(row["target_capture_id"]).strip())

    allowed = _ACTIVE_AGENTS.get(object_type)
    params: list[Any] = list(related)
    agent_clause = ""
    if allowed:
        placeholders = ", ".join("?" for _ in allowed)
        agent_clause = f" AND ar.agent IN ({placeholders})"
        params.extend(sorted(allowed))
    params.append(limit)

    rows: list[sqlite3.Row] = []
    placeholders = ", ".join("?" for _ in related)
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_run_objects'"
    ).fetchone():
        rows = conn.execute(
            f"""SELECT DISTINCT ar.* FROM agent_runs ar
                JOIN agent_run_objects aro ON aro.run_id = ar.id
                WHERE aro.object_id IN ({placeholders}){agent_clause}
                ORDER BY datetime(replace(substr(ar.updated_at, 1, 19), 'T', ' ')) DESC,
                         ar.rowid DESC
                LIMIT ?""",
            params,
        ).fetchall()

    existing = {str(row["id"] or "") for row in rows}
    like_clauses = []
    like_params: list[Any] = []
    for related_id in related:
        pattern = f"%{related_id}%"
        like_clauses.append(
            "(input_json LIKE ? OR output_json LIKE ? OR evidence_json LIKE ?)"
        )
        like_params.extend([pattern, pattern, pattern])
    agent_clause = ""
    if allowed:
        agent_clause = f" AND agent IN ({', '.join('?' for _ in allowed)})"
        like_params.extend(sorted(allowed))
    exclude = ""
    if existing:
        exclude = f" AND id NOT IN ({', '.join('?' for _ in existing)})"
        like_params.extend(sorted(existing))
    like_params.append(max(1, limit - len(rows)))
    fallback = conn.execute(
        f"""SELECT * FROM agent_runs
            WHERE ({' OR '.join(like_clauses)}){agent_clause}{exclude}
            ORDER BY datetime(replace(substr(updated_at, 1, 19), 'T', ' ')) DESC,
                     rowid DESC
            LIMIT ?""",
        like_params,
    ).fetchall()
    return [*rows, *fallback][:limit], related


def _run_event_view(row: sqlite3.Row) -> dict:
    data = dict(row)
    return {
        "id": data.get("id", ""),
        "agent": data.get("agent", ""),
        "intent": data.get("intent", ""),
        "trigger": data.get("trigger", ""),
        "status": data.get("status", ""),
        "input": _json_value(str(data.get("input_json") or "{}"), {}),
        "output": _json_value(str(data.get("output_json") or "{}"), {}),
        "evidence": _json_value(str(data.get("evidence_json") or "{}"), {}),
        "warnings": _json_value(str(data.get("warnings_json") or "[]"), []),
        "errors": _json_value(str(data.get("errors_json") or "[]"), []),
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", ""),
        "completed_at": data.get("completed_at", ""),
    }


def create_dashboard_read_router(
    capture_repo,
    reading_repo,
    vault_path: str | Path,
) -> APIRouter:
    router = APIRouter()
    vault = Path(vault_path).expanduser()

    @router.get("/knowledge/capture-drafts")
    def list_capture_drafts(limit: int = Query(default=50, ge=1, le=200)):
        rows = capture_repo.conn.execute(
            """SELECT * FROM capture_drafts
               WHERE COALESCE(status, '') NOT IN ('abandoned', 'discarded')
               ORDER BY datetime(replace(substr(updated_at, 1, 19), 'T', ' ')) DESC,
                        rowid DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        drafts = [_capture_draft_view(row) for row in rows]
        return {
            "drafts": drafts,
            "total": len(drafts),
            "open_count": len(drafts),
            "items": drafts,
        }

    @router.get("/knowledge/capture-drafts/{draft_id}")
    def get_capture_draft(draft_id: str):
        row = capture_repo.conn.execute(
            "SELECT * FROM capture_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Capture draft not found")
        return {"draft": _capture_draft_view(row, full=True)}

    @router.get("/knowledge/reading-drafts")
    def list_reading_drafts(limit: int = Query(default=50, ge=1, le=200)):
        rows = reading_repo.conn.execute(
            "SELECT * FROM reading_drafts WHERE status = 'open' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        from api.routes.reading import _deserialize_meta, _strip_binary

        drafts = [_strip_binary(_deserialize_meta(dict(row))) for row in rows]
        return {
            "drafts": drafts,
            "total": len(drafts),
            "open_count": len(drafts),
            "items": drafts,
        }

    @router.get("/knowledge/reading-drafts/{draft_id}")
    def get_reading_draft(draft_id: str):
        from api.routes.reading import _deserialize_meta, _strip_binary

        draft = reading_repo.get_draft(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="Reading draft not found")
        return {"draft": _strip_binary(_deserialize_meta(draft))}

    @router.get("/knowledge/object-events")
    def object_events(
        object_type: str = Query(min_length=1),
        object_id: str = Query(min_length=1),
        limit: int = Query(default=30, ge=1, le=100),
    ):
        rows, related = _agent_runs_for_object(
            capture_repo.conn, object_type, object_id, limit
        )
        events = [
            _run_event_view(row)
            for row in rows
            if not _ACTIVE_AGENTS.get(object_type)
            or str(row["agent"] or "") in _ACTIVE_AGENTS[object_type]
        ]
        return {"events": events, "related_ids": related, "total": len(events)}

    @router.get("/knowledge/cards/{card_id}")
    def get_card(card_id: str):
        row = capture_repo.conn.execute(
            "SELECT * FROM garden_cards WHERE id = ?", (card_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Knowledge card not found")
        return {"card": _card_view(capture_repo.conn, vault, row)}

    @router.get("/knowledge/library/items/{kind}/{item_id}/dependencies")
    def item_dependencies(kind: str, item_id: str):
        if kind != "capture":
            return {
                "ok": True,
                "id": item_id,
                "dependencies": {
                    "garden_cards": [],
                    "garden_drafts": [],
                    "committed_drafts": [],
                    "backlinks": [],
                    "event_count": 0,
                    "policy": {
                        "action": "delete_draft_only",
                        "database_row": "removed_or_marked_discarded_by_capture_bridge",
                    },
                },
            }
        dependencies = capture_dependencies(capture_repo.conn, item_id)
        dependencies["event_count"] = capture_repo.conn.execute(
            "SELECT COUNT(*) FROM agent_runs WHERE input_json LIKE ? "
            "OR output_json LIKE ? OR evidence_json LIKE ?",
            (f"%{item_id}%", f"%{item_id}%", f"%{item_id}%"),
        ).fetchone()[0]
        dependencies.setdefault(
            "policy",
            {
                "action": "archive_capture",
                "database_row": "kept; archived_at set",
            },
        )
        return {"ok": True, "id": item_id, "dependencies": dependencies}

    return router
