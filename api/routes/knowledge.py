"""Knowledge card and answer draft API routes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agents.knowledge_graph import KnowledgeGraphService
from api.routes._bridge_response import bridge_result_response
from api.routes._confirmation import issue_confirmation_token, require_confirmation_token
from db.confirmation_repo import ConfirmationRepo
from db.concurrency import transaction
from db.reading_repo import ReadingRepo
from db.repo import CaptureRepo

COMMIT_KNOWLEDGE_DRAFT_INTENT = "commit_reading_artifact"
PERMANENT_DELETE_INTENT = "permanent_delete_library_item"
# Tables whose ``capture_id`` column points only at captures, so a plain delete
# is enough.  Other dependents (drafts, edges, questions, run objects, review
# items) need narrower predicates and live in ``_delete_capture_dependents``.
_DELETABLE_CAPTURE_TABLES = (
    ("capture_embeddings", "capture_id"),
    ("capture_metadata", "capture_id"),
    ("capture_version_backups", "capture_id"),
)


class PermanentDeleteRequest(BaseModel):
    confirmation_token: str = Field(default="", max_length=200)


class AnswerDraftRequest(BaseModel):
    hermes_session_id: str = ""
    user_message: str = ""
    assistant_answer: str = ""
    instruction: str = ""
    output_type: str = "auto"
    sources: list[dict[str, Any]] = []
    target_capture_id: str = ""
    folder_path: str = ""


class ReviseDraftRequest(BaseModel):
    instruction: str = ""


class CommitKnowledgeDraftRequest(BaseModel):
    confirmation_token: str = Field(default="", max_length=200)


def create_knowledge_router(
    reading_repo: ReadingRepo,
    capture_repo: CaptureRepo,
    vault_path: str = "~/Hermes/knowledge",
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
            folder_path=body.folder_path,
        )
        return bridge_result_response(result)

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
        return bridge_result_response(result)

    @router.post("/knowledge/drafts/{draft_id}/confirmation")
    def issue_knowledge_draft_confirmation(draft_id: str):
        draft = reading_repo.get_draft(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="not found")
        return issue_confirmation_token(
            ConfirmationRepo(reading_repo.conn),
            intent=COMMIT_KNOWLEDGE_DRAFT_INTENT,
            object_id=draft_id,
        )

    @router.post("/knowledge/drafts/{draft_id}/commit")
    def commit_draft(
        draft_id: str,
        body: CommitKnowledgeDraftRequest | None = None,
    ):
        from bridge import reading_bridge

        require_confirmation_token(
            ConfirmationRepo(reading_repo.conn),
            intent=COMMIT_KNOWLEDGE_DRAFT_INTENT,
            object_id=draft_id,
            token=body.confirmation_token if body else "",
        )
        result = reading_bridge.commit_reading_artifact(
            draft_id,
            confirmation_source="api",
            token_verified=True,
        )
        return bridge_result_response(result)

    @router.post("/knowledge/library/items/{kind}/{item_id}/confirmation")
    def issue_library_permanent_delete_confirmation(kind: str, item_id: str):
        kind = (kind or "").strip()
        if kind != "capture":
            raise HTTPException(status_code=400, detail="only committed capture files can be permanently deleted")
        if capture_repo.get_by_id(item_id) is None:
            raise HTTPException(status_code=404, detail="not found")
        return issue_confirmation_token(
            ConfirmationRepo(capture_repo.conn),
            intent=PERMANENT_DELETE_INTENT,
            object_id=_delete_object_id(kind, item_id),
        )

    @router.delete("/knowledge/library/items/{kind}/{item_id}/permanent")
    def permanent_delete_library_item(
        kind: str,
        item_id: str,
        body: PermanentDeleteRequest | None = None,
    ):
        kind = (kind or "").strip()
        if kind != "capture":
            raise HTTPException(status_code=400, detail="only committed capture files can be permanently deleted")
        require_confirmation_token(
            ConfirmationRepo(capture_repo.conn),
            intent=PERMANENT_DELETE_INTENT,
            object_id=_delete_object_id(kind, item_id),
            token=body.confirmation_token if body else "",
        )
        conn = capture_repo.conn
        # Reference check and deletes share one transaction: a card created
        # between the check and the delete must not lose its source silently.
        with transaction(conn):
            row = capture_repo.get_by_id(item_id)
            if row is None:
                raise HTTPException(status_code=404, detail="not found")
            references = _garden_card_references(conn, item_id)
            if references:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "referenced", "message": "capture is referenced by knowledge cards", "references": references},
                )
            backup_paths = _capture_backup_paths(conn, item_id)
            _delete_capture_dependents(conn, item_id)
            conn.execute("DELETE FROM captures WHERE id = ?", (item_id,))

        vault = Path(vault_path).expanduser()
        file_path = str(row.get("file_path") or "")
        raw_path = str(row.get("raw_path") or "")
        warnings: list[str] = []
        deleted_file = _unlink_inside_vault(vault, file_path, warnings)
        deleted_raw = ""
        if raw_path and raw_path != file_path:
            deleted_raw = _unlink_inside_vault(vault, raw_path, warnings)
        for backup_path in backup_paths:
            _unlink_inside_vault(vault, backup_path, warnings)
        _delete_sibling_backups(vault, file_path, warnings)
        # ``ok`` only when nothing was left behind: a partially deleted item
        # still has files on disk and callers must not report success.
        return {
            "ok": not warnings,
            "status": "partially_deleted" if warnings else "permanently_deleted",
            "id": item_id,
            "deleted": {"knowledge_file": deleted_file, "raw_file": deleted_raw},
            "warnings": warnings,
        }

    @router.get("/knowledge/search")
    def search_knowledge_base(
        q: str = Query(min_length=1, max_length=200),
        limit: int = Query(default=10, ge=1, le=50),
    ):
        from bridge.knowledge_search import search_captures

        items = search_captures(vault_path, capture_repo, q, limit=limit)
        return {
            "query": q,
            "limit": limit,
            "total": len(items),
            "items": [_strip_binary(item) for item in items],
        }

    @router.get("/knowledge/garden-cards")
    def list_garden_cards(limit: int = Query(default=50, le=200)):
        rows = capture_repo.conn.execute(
            "SELECT * FROM garden_cards WHERE status IN ('seed', 'accepted') ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        items = [_strip_binary(dict(row)) for row in rows]
        return {"items": items}

    @router.post("/knowledge/graph/refresh")
    def refresh_knowledge_graph():
        return KnowledgeGraphService(capture_repo.conn, vault_path).refresh()

    @router.get("/knowledge/graph")
    def get_knowledge_graph(
        focus_kind: str = Query(default=""),
        focus_id: str = Query(default=""),
        depth: int = Query(default=1, ge=0, le=1),
        limit: int = Query(default=80, ge=1, le=200),
    ):
        return KnowledgeGraphService(capture_repo.conn, vault_path).focus_graph(
            focus_kind=focus_kind,
            focus_id=focus_id,
            depth=depth,
            limit=limit,
        )

    return router


def _strip_binary(d: dict) -> dict:
    """Remove non-JSON-serializable binary fields."""
    return {k: v for k, v in d.items() if not isinstance(v, bytes)}


def _delete_object_id(kind: str, item_id: str) -> str:
    return f"{kind}:{item_id}"


def _garden_card_references(conn, capture_id: str) -> list[str]:
    """Return garden card ids that reference the capture (safety gate)."""
    try:
        rows = conn.execute(
            """SELECT gc.id FROM garden_cards gc, json_each(gc.capture_ids) je
               WHERE je.value = ?""",
            (capture_id,),
        ).fetchall()
        return [str(row["id"]) for row in rows]
    except Exception:
        rows = conn.execute(
            "SELECT id FROM garden_cards WHERE capture_ids LIKE ?",
            (f'%"{capture_id}"%',),
        ).fetchall()
        return [str(row["id"]) for row in rows]


def _table_columns(conn, table: str) -> set[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {str(row["name"]) for row in rows}


def _capture_backup_paths(conn, capture_id: str) -> list[str]:
    """Collect the vault-relative backup files recorded for this capture."""
    rows = conn.execute(
        "SELECT backup_file_path FROM capture_version_backups WHERE capture_id = ?",
        (capture_id,),
    ).fetchall()
    return [
        str(row["backup_file_path"])
        for row in rows
        if str(row["backup_file_path"] or "").strip()
    ]


def _delete_capture_dependents(conn, capture_id: str) -> None:
    """Delete or unlink every row that points at the capture.

    Runs inside the caller's transaction and before the captures row itself is
    removed, so the database never keeps dangling references to a deleted
    capture.
    """
    conn.execute(
        """UPDATE capture_drafts
           SET committed_capture_id = '',
               pending_capture_id = '',
               status = CASE WHEN status = 'committed' THEN 'draft_ready' ELSE status END
           WHERE committed_capture_id = ? OR pending_capture_id = ?""",
        (capture_id, capture_id),
    )
    conn.execute(
        "UPDATE reading_drafts SET target_capture_id = '' WHERE target_capture_id = ?",
        (capture_id,),
    )
    conn.execute(
        "DELETE FROM reading_evidence WHERE source_kind = 'capture' AND source_id = ?",
        (capture_id,),
    )
    conn.execute(
        """DELETE FROM knowledge_edges
           WHERE (source_kind = 'capture' AND source_id = ?)
              OR (target_kind = 'capture' AND target_id = ?)""",
        (capture_id, capture_id),
    )
    _remove_capture_from_pending_questions(conn, capture_id)
    from db.embedding_repo import delete_fts_for_capture

    delete_fts_for_capture(conn, capture_id)
    for table, column in _DELETABLE_CAPTURE_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (capture_id,))

    run_object_columns = _table_columns(conn, "agent_run_objects")
    if "object_type" in run_object_columns:
        conn.execute(
            "DELETE FROM agent_run_objects "
            "WHERE object_type = 'capture' AND object_id = ?",
            (capture_id,),
        )
    elif "object_id" in run_object_columns:
        conn.execute(
            "DELETE FROM agent_run_objects WHERE object_id = ?",
            (capture_id,),
        )

    review_columns = _table_columns(conn, "review_items")
    if "capture_id" in review_columns and "object_type" in review_columns and "object_id" in review_columns:
        conn.execute(
            """DELETE FROM review_items
               WHERE capture_id = ?
                  OR (object_type = 'capture' AND object_id = ?)""",
            (capture_id, capture_id),
        )
    elif "capture_id" in review_columns:
        conn.execute("DELETE FROM review_items WHERE capture_id = ?", (capture_id,))


def _remove_capture_from_pending_questions(conn, capture_id: str) -> None:
    """Drop the capture id from the JSON array in pending_questions."""
    conn.execute(
        """UPDATE pending_questions
           SET related_capture_ids = COALESCE(
               (
                   SELECT json_group_array(je.value)
                   FROM json_each(
                       CASE
                           WHEN json_valid(pending_questions.related_capture_ids)
                           THEN pending_questions.related_capture_ids
                           ELSE '[]'
                       END
                   ) AS je
                   WHERE je.value <> ?
               ),
               '[]'
           )
           WHERE EXISTS (
               SELECT 1 FROM json_each(
                   CASE
                       WHEN json_valid(pending_questions.related_capture_ids)
                       THEN pending_questions.related_capture_ids
                       ELSE '[]'
                   END
               ) AS je
               WHERE je.value = ?
           )""",
        (capture_id, capture_id),
    )


def _delete_sibling_backups(vault: Path, file_path: str, warnings: list[str]) -> None:
    """Remove ``<note>.<ext>.bak-*`` copies kept next to the knowledge file.

    ``LibraryAgent._backup_markdown_file`` writes those siblings; they would
    otherwise outlive the note they belong to.
    """
    from bridge.atomic_io import resolve_inside

    if not file_path:
        return
    try:
        target = resolve_inside(vault, file_path)
    except ValueError:
        return
    try:
        siblings = sorted(target.parent.glob(f"{target.name}.bak-*"))
    except OSError as exc:
        warnings.append(f"could not list backups for {file_path}: {exc}")
        return
    for sibling in siblings:
        try:
            if sibling.is_file():
                sibling.unlink()
        except OSError as exc:
            warnings.append(f"could not delete backup {sibling.name}: {exc}")


def _unlink_inside_vault(vault: Path, file_path: str, warnings: list[str]) -> str:
    from bridge.atomic_io import resolve_inside

    if not file_path:
        return ""
    try:
        target = resolve_inside(vault, file_path)
    except ValueError:
        warnings.append(f"skipped path outside vault: {file_path}")
        return ""
    try:
        if target.is_file():
            target.unlink()
        else:
            # Report only files that really went away; a missing file is not a
            # deletion (the old code echoed the path anyway).
            return ""
    except OSError as exc:
        warnings.append(f"could not delete {file_path}: {exc}")
        return ""
    return file_path
