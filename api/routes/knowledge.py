"""Knowledge card and answer draft API routes."""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
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
    assistant_message_id: str = ""
    answer_source: str = "auto"
    source_run_id: str = ""
    sources: list[dict[str, Any]] = []
    target_capture_id: str = ""
    folder_path: str = ""


class ReviseDraftRequest(BaseModel):
    instruction: str = ""


class CommitKnowledgeDraftRequest(BaseModel):
    confirmation_token: str = Field(default="", max_length=200)


class LibrarySelectionChatRequest(BaseModel):
    question: str
    selected_text: str = ""
    surrounding_before: str = ""
    surrounding_after: str = ""
    heading_path: list[str] = []
    selection_start: int | None = None
    selection_end: int | None = None
    selection_confidence: str = "none"
    mode: str = "local_web"
    history: list[dict[str, str]] = []
    history_summary: str = ""
    history_summary_version: int = 0
    history_overflow: list[dict[str, str]] = []
    force_web: bool = False


class ResearchStreamRequest(BaseModel):
    query: str = Field(..., min_length=1)
    mode: str = "local_web"
    article_context: dict[str, Any] | None = None
    selection_context: dict[str, Any] | None = None
    export_file: bool = False
    force_web: bool = False
    channel: str = ""


def _sse_event(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def _supports_event_callback(func: Any) -> bool:
    """True when ``func`` accepts the research ``event_callback`` keyword."""
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    return "event_callback" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


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
            assistant_message_id=body.assistant_message_id,
            answer_source=body.answer_source,
            source_run_id=body.source_run_id,
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

    def _selection_chat_kwargs(
        kind: str, item_id: str, body: LibrarySelectionChatRequest
    ) -> dict[str, Any]:
        if not body.question.strip():
            raise HTTPException(status_code=400, detail="question is required")
        if len((body.selected_text or "").strip()) > 1000:
            raise HTTPException(
                status_code=400,
                detail="选区超过 1000 字，当前不支持基于超长选区检索。请缩短选区后再试。",
            )
        from bridge.library_view import get_library_item

        item = get_library_item(capture_repo.conn, vault_path, kind, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Library item not found")
        return {
            "query": body.question.strip(),
            "mode": body.mode,
            "article_context": {
                "kind": kind,
                "id": item_id,
                "title": item.get("title") or "",
                "body": item.get("body") or item.get("preview") or "",
                "folder_path": item.get("folder_path") or "Inbox",
                "summary": item.get("summary") or "",
                "tags": item.get("tags") or [],
                "file_path": item.get("file_path") or "",
            },
            "selection_context": {
                "selected_text": body.selected_text,
                "surrounding_before": body.surrounding_before,
                "surrounding_after": body.surrounding_after,
                "heading_path": body.heading_path,
                "selection_start": body.selection_start,
                "selection_end": body.selection_end,
                "selection_confidence": body.selection_confidence,
            },
            "history": body.history,
            "history_summary": body.history_summary,
            "history_summary_version": body.history_summary_version,
            "history_overflow": body.history_overflow,
            "force_web": body.force_web,
        }

    @router.post("/knowledge/library/items/{kind}/{item_id}/selection-chat")
    async def selection_chat(
        kind: str, item_id: str, body: LibrarySelectionChatRequest
    ):
        from bridge.research_bridge import async_web_assist_answer, web_assist_answer

        kwargs = _selection_chat_kwargs(kind, item_id, body)
        try:
            return await async_web_assist_answer(**kwargs)
        except TypeError:
            # Old checkouts expose only the sync facade.
            return await asyncio.to_thread(web_assist_answer, **kwargs)

    @router.post("/knowledge/library/items/{kind}/{item_id}/selection-chat/stream")
    async def selection_chat_stream(
        kind: str,
        item_id: str,
        body: LibrarySelectionChatRequest,
        request: Request,
    ):
        """SSE variant of ``selection-chat``.

        Token deltas (``answer_delta``) and research progress are forwarded as
        they happen so the dashboard sidebar can render the answer while the
        synthesis LLM is still writing.  Without this route the caller only
        sees the finished answer after 20-60 seconds.
        """
        from bridge.research_bridge import async_web_assist_answer, web_assist_answer

        kwargs = _selection_chat_kwargs(kind, item_id, body)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def on_event(event: dict[str, Any]) -> None:
            await queue.put({"event": "progress", **event})

        async def run_answer() -> None:
            try:
                if _supports_event_callback(async_web_assist_answer):
                    result = await async_web_assist_answer(
                        **kwargs, event_callback=on_event
                    )
                else:
                    # Old checkouts expose only the sync facade, which cannot
                    # stream: the caller still gets the final answer.
                    result = await asyncio.to_thread(web_assist_answer, **kwargs)
                await queue.put({"event": "complete", "result": result})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the stream reports failures
                await queue.put({"event": "error", "message": str(exc)})

        task = asyncio.create_task(run_answer())

        async def event_stream():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    yield _sse_event(event)
                    if event.get("event") in {"complete", "error"}:
                        break
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/knowledge/research/stream")
    async def research_stream(body: ResearchStreamRequest, request: Request):
        """SSE variant of ``research_with_web`` forwarding progress and answer deltas."""
        from bridge.research_bridge import async_web_assist_answer, web_assist_answer

        kwargs: dict[str, Any] = {
            "query": body.query,
            "mode": body.mode,
            "article_context": body.article_context,
            "selection_context": body.selection_context,
            "export_file": body.export_file,
            "force_web": body.force_web,
        }
        if body.channel:
            kwargs["channel"] = body.channel
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def on_event(event: dict[str, Any]) -> None:
            await queue.put({"event": "progress", **event})

        async def run_research() -> None:
            try:
                if _supports_event_callback(async_web_assist_answer):
                    result = await async_web_assist_answer(
                        **kwargs, event_callback=on_event
                    )
                else:
                    result = await asyncio.to_thread(web_assist_answer, **kwargs)
                await queue.put({"event": "complete", "result": result})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the stream reports failures
                await queue.put({"event": "error", "message": str(exc)})

        task = asyncio.create_task(run_research())

        async def event_stream():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    yield _sse_event(event)
                    if event.get("event") in {"complete", "error"}:
                        break
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/knowledge/garden-cards")
    def list_garden_cards(limit: int = Query(default=50, le=200)):
        rows = capture_repo.conn.execute(
            "SELECT * FROM garden_cards WHERE status IN ('seed', 'accepted') ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        items = [_strip_binary(dict(row)) for row in rows]
        return {"items": items}

    @router.post("/knowledge/ai-operations/{operation_id}/cancel")
    def cancel_ai_operation(operation_id: str):
        from bridge.ai_cancel import cancel_operation, normalize_operation_id

        normalized = normalize_operation_id(operation_id)
        return {
            "ok": bool(cancel_operation(normalized)),
            "operation_id": normalized,
            "status": "cancel_requested",
        }

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
