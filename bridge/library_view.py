"""Library item views for the contract v1.1 write endpoints.

The dashboard consumes library items in HTTP mode, so the field shape mirrors
the runtime's local library item builder.  Hydration reuses
:mod:`bridge.capture_view` and the draft rows produced by ``CaptureDraftRepo`` —
this module only adds the derived fields (``kind``/``preview``/``indexed``/...)
both sides already agreed on.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from bridge.atomic_io import resolve_inside
from bridge.capture_view import build_capture_view, read_capture_note
from bridge.folder_store import normalize_folder, visible_folders
from bridge.knowledge_write_service import KnowledgeWriteService, MarkdownWriteRequest
from db.concurrency import transaction

PREVIEW_LIMIT = 360
OPEN_DRAFT_STATUSES = frozenset(
    {"raw_extracted", "draft_ready", "revision_requested", "draft_incomplete"}
)
_HIDDEN_DRAFT_STATUSES = frozenset({"abandoned", "discarded"})
_ACTIVE_GARDEN_DRAFT_STATUSES = frozenset({"draft_ready", "revision_requested", "open"})
_DEPENDENCY_POLICY = {
    "action": "archive_capture_only",
    "knowledge_file": "retained",
    "database_row": "retained_with_review_state_archived",
    "garden_cards": "retained",
    "garden_drafts": "retained",
    "committed_drafts": "retained",
    "backlinks": "retained",
}


def clip_text(text: object, limit: int = PREVIEW_LIMIT) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def capture_library_item(
    vault_path: str | Path,
    row: dict,
    *,
    conn=None,
    full: bool = False,
    indexed_ids: set[str] | None = None,
    garden_counts: dict[str, int] | None = None,
) -> dict:
    """Build the dashboard's ``capture`` library item from one capture row."""
    frontmatter, body = read_capture_note(vault_path, row)
    data = build_capture_view(row, frontmatter, body)
    capture_id = str(data.get("id") or "")
    file_path = str(data.get("file_path") or "")
    title = (
        str(data.get("title") or "").strip()
        or (Path(file_path).stem if file_path else "")
        or capture_id
        or "未命名"
    )
    summary = str(data.get("summary") or "").strip()
    return {
        "id": capture_id,
        "kind": "capture",
        "title": title,
        "summary": summary,
        "preview": clip_text(summary or body),
        "body": body if full else "",
        "folder_path": normalize_folder(data.get("folder_path")),
        "tags": list(data.get("tags") or []),
        "source": str(data.get("source") or ""),
        "source_type": str(data.get("source_type") or ""),
        "status": str(data.get("status") or data.get("review_state") or "captured"),
        "file_path": file_path,
        "raw_path": str(data.get("raw_path") or ""),
        "word_count": int(data.get("word_count") or len(body)),
        "created_at": str(data.get("created_at") or ""),
        "updated_at": str(data.get("updated_at") or ""),
        "indexed": (
            capture_id in indexed_ids
            if indexed_ids is not None
            else _capture_is_indexed(conn, capture_id, data.get("embedding"))
        ),
        "title_source": str(frontmatter.get("title_source") or "").strip(),
        "garden_card_count": (
            int(garden_counts.get(capture_id, 0))
            if garden_counts is not None
            else _garden_card_count(conn, capture_id)
        ),
    }


def draft_library_item(
    draft: dict,
    *,
    conn=None,
    full: bool = False,
    garden_counts: dict[str, int] | None = None,
) -> dict:
    """Build the dashboard's ``capture_draft`` library item from a draft row."""
    capture_intent = _json_dict(draft.get("capture_intent"))
    draft_text = str(draft.get("draft_text") or "")
    raw_text = str(draft.get("raw_text") or "")
    body = draft_text or raw_text
    summary = str(draft.get("summary") or "").strip()
    committed_capture_id = str(draft.get("committed_capture_id") or "")
    return {
        "id": str(draft.get("id") or ""),
        "kind": "capture_draft",
        "title": str(draft.get("title") or "").strip() or "未命名",
        "summary": summary,
        "preview": clip_text(summary or body),
        "body": body if full else "",
        "folder_path": normalize_folder(capture_intent.get("folder_path")),
        "tags": list(draft.get("tags") or []),
        "source": str(draft.get("source") or ""),
        "source_type": str(draft.get("source_type") or ""),
        "status": str(draft.get("status") or ""),
        "file_path": "",
        "raw_path": "",
        "word_count": int(draft.get("word_count") or len(body)),
        "created_at": str(draft.get("created_at") or ""),
        "updated_at": str(draft.get("updated_at") or ""),
        "indexed": False,
        "title_source": str(capture_intent.get("title_source") or "").strip(),
        "committed_capture_id": committed_capture_id,
        "garden_card_count": (
            int(garden_counts.get(committed_capture_id, 0))
            if garden_counts is not None
            else _garden_card_count(conn, committed_capture_id)
        ),
    }


def capture_dependencies(conn, capture_id: str) -> dict:
    """Minimal dependency summary for archiving a capture (retains everything)."""
    return {
        "garden_cards": _referencing_rows(
            conn, "garden_cards", capture_id, exclude_statuses={"archived"}
        ),
        "garden_drafts": _referencing_rows(
            conn,
            "garden_drafts",
            capture_id,
            include_statuses=_ACTIVE_GARDEN_DRAFT_STATUSES,
        ),
        "committed_drafts": [
            {
                "id": str(row["id"] or ""),
                "title": str(row["title"] or ""),
                "status": str(row["status"] or ""),
            }
            for row in conn.execute(
                "SELECT id, title, status FROM capture_drafts WHERE committed_capture_id = ?",
                (capture_id,),
            ).fetchall()
        ],
        "backlinks": [],
        "event_count": _event_count(conn, capture_id),
        "policy": dict(_DEPENDENCY_POLICY),
    }


def folder_response(conn, vault_path: str | Path) -> dict:
    """Return ``{"folders": [...], "smart_views": [...]}`` for the dashboard."""
    visible = visible_folders(vault_path)
    counts: dict[str, int] = {folder: 0 for folder in visible}
    capture_total = 0
    capture_ai_title = 0
    capture_indexed = 0
    rows = conn.execute(
        "SELECT * FROM captures WHERE COALESCE(review_state, '') != 'archived'"
    ).fetchall()
    for row in rows:
        data = dict(row)
        frontmatter, _body = read_capture_note(vault_path, data)
        folder = normalize_folder(frontmatter.get("folder_path") or frontmatter.get("folder"))
        counts[folder] = counts.get(folder, 0) + 1
        capture_total += 1
        if str(frontmatter.get("title_source") or "").strip() == "ai":
            capture_ai_title += 1
        if _capture_is_indexed(conn, str(data.get("id") or ""), data.get("embedding")):
            capture_indexed += 1

    drafts = _open_draft_rows(conn)
    for row in drafts:
        intent = _json_dict(row["capture_intent"])
        folder = normalize_folder(intent.get("folder_path"))
        counts[folder] = counts.get(folder, 0) + 1

    folders = [
        {"path": folder, "name": folder, "count": counts.get(folder, 0)}
        for folder in visible
    ]
    for folder in sorted(item for item in counts if item not in visible):
        folders.append({"path": folder, "name": folder, "count": counts[folder]})

    smart_views = [
        {"key": "recent", "label": "最近入库", "count": capture_total + len(drafts)},
        {
            "key": "review",
            "label": "待审阅",
            "count": sum(
                1 for row in drafts if str(row["status"] or "") in OPEN_DRAFT_STATUSES
            ),
        },
        {
            "key": "suggestions",
            "label": "有 AI 建议",
            "count": len(drafts) + capture_ai_title,
        },
        {
            "key": "unindexed",
            "label": "尚未检索",
            "count": (capture_total - capture_indexed) + len(drafts),
        },
    ]
    return {"folders": folders, "smart_views": smart_views}


def rename_folder_everywhere(
    conn,
    vault_path: str | Path,
    old_folder: str,
    new_folder: str,
) -> int:
    """Move capture notes and drafts from ``old_folder`` to ``new_folder``."""
    vault = Path(vault_path).expanduser()
    write_service = KnowledgeWriteService(vault)
    draft_updates: list[tuple[str, str]] = []
    file_updates: list[dict] = []

    # Phase 1: collect plan without holding a write transaction across file I/O.
    draft_rows = conn.execute(
        """SELECT * FROM capture_drafts
           WHERE COALESCE(committed_capture_id, '') = ''
             AND COALESCE(status, '') NOT IN ('abandoned', 'discarded')"""
    ).fetchall()
    for row in draft_rows:
        data = dict(row)
        intent = _json_dict(data.get("capture_intent"))
        if normalize_folder(intent.get("folder_path")) != old_folder:
            continue
        intent["folder_path"] = new_folder
        draft_updates.append(
            (json.dumps(intent, ensure_ascii=False), str(data.get("id") or ""))
        )

    capture_rows = conn.execute(
        "SELECT * FROM captures WHERE COALESCE(review_state, '') != 'archived'"
    ).fetchall()
    for row in capture_rows:
        data = dict(row)
        file_path = str(data.get("file_path") or "")
        if not file_path:
            continue
        try:
            target = resolve_inside(vault, file_path)
        except ValueError:
            continue
        if not target.is_file():
            continue
        frontmatter, body = read_capture_note(vault, data)
        if normalize_folder(frontmatter.get("folder_path") or frontmatter.get("folder")) != old_folder:
            continue
        frontmatter["folder_path"] = new_folder
        file_updates.append({
            "capture_id": str(data.get("id") or ""),
            "file_path": file_path,
            "frontmatter": frontmatter,
            "body": body,
        })

    # Phase 2: write files outside the DB transaction.  A slow disk no longer
    # blocks every other writer behind BEGIN IMMEDIATE.
    for item in file_updates:
        write_service.write_markdown_artifact(
            MarkdownWriteRequest(
                kind="capture_note",
                relative_path=item["file_path"],
                content=render_markdown(item["frontmatter"], item["body"]),
                db_object_id=item["capture_id"],
                db_table="captures",
                requires_backup=True,
            )
        )

    # Phase 3: commit the index changes in one short transaction.
    with transaction(conn):
        for intent_json, draft_id in draft_updates:
            conn.execute(
                "UPDATE capture_drafts SET capture_intent = ?, updated_at = ? WHERE id = ?",
                (intent_json, now_iso(), draft_id),
            )
        for item in file_updates:
            conn.execute(
                "UPDATE captures SET updated_at = ? WHERE id = ?",
                (now_iso(), item["capture_id"]),
            )
    return len(draft_updates) + len(file_updates)


def render_markdown(frontmatter: dict, body: str) -> str:
    return (
        "---\n"
        + yaml.dump(frontmatter, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + body
    )


def list_library_items(
    conn,
    vault_path: str | Path,
    *,
    kind: str = "",
    folder: str = "",
    status: str = "",
    search: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List the dashboard's library items (contract v1.1 ``GET /library/items``).

    Mirrors the runtime's local library listing: captures come from
    ``review_state != archived`` rows, drafts from non-committed/non-discarded
    rows; the merged list is sorted by ``updated_at`` desc then ``id`` desc.
    ``counts`` comes from the same filtered pass (before the ``kind`` filter), so
    tab counts always match the pageable set without extra queries.
    """
    kind_filter = (kind or "").strip()
    folder_filter = normalize_folder(folder) if (folder or "").strip() else ""
    status_filter = (status or "").strip()
    search_text = (search or "").strip().lower()

    if not folder_filter and not status_filter and not search_text:
        return _list_unfiltered(
            conn,
            vault_path,
            kind_filter=kind_filter,
            limit=limit,
            offset=offset,
        )

    items: list[dict] = []
    indexed_ids = _indexed_capture_ids(conn)
    garden_counts = _garden_card_counts(conn)
    for row in conn.execute(
        "SELECT * FROM captures WHERE COALESCE(review_state, '') != 'archived'"
    ).fetchall():
        # ``full=True`` keeps the note body available for the search filter;
        # list responses blank it out again below (same shape as the detail
        # view, matching the runtime's non-full library item).
        items.append(
            capture_library_item(
                vault_path,
                dict(row),
                conn=conn,
                full=True,
                indexed_ids=indexed_ids,
                garden_counts=garden_counts,
            )
        )
    for row in _open_draft_rows(conn):
        items.append(
            draft_library_item(row, conn=conn, full=True, garden_counts=garden_counts)
        )

    filtered: list[dict] = []
    for item in items:
        if folder_filter and normalize_folder(item.get("folder_path")) != folder_filter:
            continue
        if status_filter and str(item.get("status") or "") != status_filter:
            continue
        if search_text and search_text not in _library_search_haystack(item):
            continue
        filtered.append(item)
    filtered.sort(key=_library_sort_key, reverse=True)

    counts = {
        "capture": sum(1 for item in filtered if item["kind"] == "capture"),
        "capture_draft": sum(
            1 for item in filtered if item["kind"] == "capture_draft"
        ),
        "all": len(filtered),
    }
    selected = filtered
    if kind_filter:
        selected = [item for item in selected if item["kind"] == kind_filter]
    total = len(selected)
    start = max(0, int(offset))
    end = start + max(0, int(limit))
    return {
        "items": [{**item, "body": ""} for item in selected[start:end]],
        "total": total,
        "counts": counts,
    }


def _list_unfiltered(
    conn,
    vault_path: str | Path,
    *,
    kind_filter: str,
    limit: int,
    offset: int,
) -> dict:
    """List the default view without hydrating every file in the vault.

    The old path read every capture note, then filtered/sorted/sliced.  With no
    folder/status/search filters we can sort and limit in SQL first, and only
    read the notes that will actually be returned.
    """
    start = max(0, int(offset))
    end = start + max(0, int(limit))
    capture_where = "COALESCE(review_state, '') != 'archived'"
    draft_where = (
        "COALESCE(committed_capture_id, '') = '' "
        "AND COALESCE(status, '') NOT IN ('abandoned', 'discarded')"
    )
    capture_total = int(
        conn.execute(f"SELECT COUNT(*) FROM captures WHERE {capture_where}").fetchone()[0]
    )
    draft_total = int(
        conn.execute(f"SELECT COUNT(*) FROM capture_drafts WHERE {draft_where}").fetchone()[0]
    )
    counts = {
        "capture": capture_total,
        "capture_draft": draft_total,
        "all": capture_total + draft_total,
    }
    if end <= 0:
        return {"items": [], "total": 0, "counts": counts}

    indexed_ids = _indexed_capture_ids(conn)
    garden_counts = _garden_card_counts(conn)
    items: list[dict] = []
    if kind_filter in {"", "capture"} and capture_total:
        rows = conn.execute(
            f"""SELECT * FROM captures
                WHERE {capture_where}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?""",
            (end,),
        ).fetchall()
        items.extend(
            capture_library_item(
                vault_path,
                dict(row),
                conn=conn,
                full=True,
                indexed_ids=indexed_ids,
                garden_counts=garden_counts,
            )
            for row in rows
        )
    if kind_filter in {"", "capture_draft"} and draft_total:
        rows = conn.execute(
            f"""SELECT * FROM capture_drafts
                WHERE {draft_where}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?""",
            (end,),
        ).fetchall()
        items.extend(
            draft_library_item(
                dict(row), conn=conn, full=True, garden_counts=garden_counts
            )
            for row in rows
        )
    if kind_filter not in {"", "capture", "capture_draft"}:
        return {"items": [], "total": 0, "counts": counts}
    items.sort(key=_library_sort_key, reverse=True)
    selected = items[start:end]
    total = counts.get(kind_filter, counts["all"]) if kind_filter else counts["all"]
    return {
        "items": [{**item, "body": ""} for item in selected],
        "total": total,
        "counts": counts,
    }


def get_library_item(conn, vault_path: str | Path, kind: str, item_id: str) -> dict | None:
    """Return one library item view or ``None`` when it does not exist."""
    kind = (kind or "").strip()
    if kind == "capture":
        row = conn.execute("SELECT * FROM captures WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            return None
        return capture_library_item(vault_path, dict(row), conn=conn, full=True)
    if kind == "capture_draft":
        row = conn.execute(
            "SELECT * FROM capture_drafts WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return None
        return draft_library_item(dict(row), conn=conn, full=True)
    return None


def _library_sort_key(item: dict) -> tuple[str, str]:
    # Rows mix ISO-8601 and ``YYYY-MM-DD HH:MM:SS``; normalise before sorting.
    updated = str(item.get("updated_at") or item.get("created_at") or "")
    return (updated.replace(" ", "T")[:19], str(item.get("id") or ""))


def _library_search_haystack(item: dict) -> str:
    return "\n".join(
        str(item.get(key) or "") for key in ("title", "summary", "body", "preview")
    ).lower()


def open_draft_rows(conn) -> list[dict]:
    """Drafts the library still shows (not committed, not discarded)."""
    return _open_draft_rows(conn)


def _open_draft_rows(conn) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            """SELECT * FROM capture_drafts
               WHERE COALESCE(committed_capture_id, '') = ''
                 AND COALESCE(status, '') NOT IN ('abandoned', 'discarded')"""
        ).fetchall()
    ]


def _capture_is_indexed(conn, capture_id: str, embedding: Any) -> bool:
    if embedding is not None:
        return True
    if not conn or not capture_id:
        return False
    try:
        row = conn.execute(
            """SELECT 1 FROM capture_embeddings
               WHERE capture_id = ? AND kind = 'identity' AND embedding IS NOT NULL
               LIMIT 1""",
            (capture_id,),
        ).fetchone()
    except Exception:  # noqa: BLE001 - indexing is advisory metadata
        return False
    return row is not None


def _indexed_capture_ids(conn) -> set[str]:
    if not conn:
        return set()
    try:
        return {
            str(row["capture_id"])
            for row in conn.execute(
                """SELECT DISTINCT capture_id FROM capture_embeddings
                   WHERE kind = 'identity' AND embedding IS NOT NULL"""
            ).fetchall()
        }
    except Exception:  # noqa: BLE001 - indexing is advisory metadata
        return set()


def _garden_card_counts(conn) -> dict[str, int]:
    """Count card references in one pass instead of one query per row."""
    counts: dict[str, int] = {}
    if not conn:
        return counts
    try:
        rows = conn.execute("SELECT capture_ids FROM garden_cards").fetchall()
    except Exception:  # noqa: BLE001 - optional table
        return counts
    for row in rows:
        for capture_id in {
            str(item) for item in _json_list(row["capture_ids"]) if str(item)
        }:
            counts[capture_id] = counts.get(capture_id, 0) + 1
    return counts


def _garden_card_count(conn, capture_id: str) -> int:
    if not conn or not capture_id:
        return 0
    try:
        rows = conn.execute(
            "SELECT capture_ids FROM garden_cards WHERE capture_ids LIKE ?",
            (f"%{capture_id}%",),
        ).fetchall()
    except Exception:  # noqa: BLE001 - advisory metadata
        return 0
    count = 0
    for row in rows:
        if capture_id in {str(item) for item in _json_list(row["capture_ids"])}:
            count += 1
    return count


def _referencing_rows(
    conn,
    table: str,
    capture_id: str,
    *,
    include_statuses: set[str] | None = None,
    exclude_statuses: set[str] | None = None,
) -> list[dict[str, str]]:
    try:
        rows = conn.execute(
            f"SELECT id, title, status, capture_ids FROM {table} WHERE capture_ids LIKE ?",
            (f"%{capture_id}%",),
        ).fetchall()
    except Exception:  # noqa: BLE001 - optional tables (garden_*) may be absent
        return []
    refs: list[dict[str, str]] = []
    for row in rows:
        status = str(row["status"] or "")
        if include_statuses is not None and status not in include_statuses:
            continue
        if exclude_statuses is not None and status in exclude_statuses:
            continue
        ids = {str(item) for item in _json_list(row["capture_ids"])}
        if capture_id not in ids:
            continue
        refs.append(
            {
                "id": str(row["id"] or ""),
                "title": str(row["title"] or ""),
                "status": status,
            }
        )
    return refs


def _event_count(conn, capture_id: str) -> int:
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT run_id) FROM agent_run_objects WHERE object_id = ?",
            (capture_id,),
        ).fetchone()
    except Exception:  # noqa: BLE001 - optional table
        return 0
    return int(row[0] or 0) if row else 0


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
