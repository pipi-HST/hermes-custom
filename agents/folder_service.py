"""Folder management domain service — authoritative folder rename logic.

This module provides the single source of truth for folder operations across
all data stores (SQLite + Markdown frontmatter).  All folder renames must go
through this service to maintain consistency.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bridge.atomic_io import resolve_inside, atomic_write_text
from bridge.folder_store import normalize_folder, rename_stored_folder
from bridge.knowledge_write_service import markdown_write_lock
from bridge.process_lock import try_process_lock
from db.concurrency import connection_lock, transaction
from db.write_job_repo import KnowledgeWriteJobRepo

logger = logging.getLogger(__name__)
_NOTE_FRONTMATTER = re.compile(r"\A(?:\ufeff)?---\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)", re.DOTALL | re.MULTILINE)


@dataclass
class FolderRenameResult:
    """Result of a folder rename operation."""
    old_folder: str
    new_folder: str
    updated_drafts: int
    updated_captures: int
    updated_garden_cards: int
    updated_metadata: int
    errors: list[str]
    warnings: list[str]

    def success(self) -> bool:
        return len(self.errors) == 0

    def total_updated(self) -> int:
        return self.updated_drafts + self.updated_captures + self.updated_garden_cards


def rename_folder(
    old_folder: str,
    new_folder: str,
    vault_path: str | Path,
    conn: sqlite3.Connection,
) -> FolderRenameResult:
    """Rename Markdown, SQLite projections and the registry, with recovery."""
    old = normalize_folder(old_folder)
    new = normalize_folder(new_folder)

    if not new:
        return FolderRenameResult(old, new, 0, 0, 0, 0, ["new folder name cannot be empty"], [])
    if old == new:
        return FolderRenameResult(old, new, 0, 0, 0, 0, [], ["old and new folder names are identical"])

    vault_root = Path(vault_path).expanduser().resolve()
    with try_process_lock(resolve_inside(vault_root, ".hermes/rename.lock")) as locked:
        if not locked:
            return FolderRenameResult(old, new, 0, 0, 0, 0, ["another topic rename is running"], [])
        with markdown_write_lock:
            return _rename_folder_locked(old, new, vault_root, conn)


def _rename_folder_locked(old: str, new: str, vault_root: Path, conn: sqlite3.Connection) -> FolderRenameResult:
    errors: list[str] = []
    warnings: list[str] = []
    updated_garden_cards = 0
    updated_drafts = 0
    written: list[str] = []

    try:
        with connection_lock(conn):
            if conn.in_transaction:
                raise RuntimeError("topic rename requires its own durable journal transaction")
        journal = conn.execute(
            """SELECT id, step, payload_json FROM knowledge_write_jobs
               WHERE intent = 'rename_folder_journal' AND status = 'pending'"""
        ).fetchone()
        if journal is not None:
            plan = json.loads(journal["payload_json"])
            if (plan["old"], plan["new"]) != (old, new):
                raise ValueError(f"resume incomplete topic rename {plan['old']!r} → {plan['new']!r} first")
            job_id = journal["id"]
            apply_db = journal["step"] != "db_applied"
            if apply_db:
                # A capture may have been saved after the earlier attempt.
                # Extend the manifest before making any additional file writes.
                with transaction(conn):
                    extra = _collect_notes(conn, vault_root, old, warnings)
                    capture_ids = list(dict.fromkeys([*plan["capture_ids"], *extra]))
                    if capture_ids != plan["capture_ids"]:
                        plan["capture_ids"] = capture_ids
                        conn.execute(
                            "UPDATE knowledge_write_jobs SET payload_json = ? WHERE id = ?",
                            (json.dumps(plan, ensure_ascii=False), job_id),
                        )
        else:
            with transaction(conn):
                files = _collect_notes(conn, vault_root, old, warnings)
                plan = {"old": old, "new": new, "capture_ids": list(files)}
                job_id = KnowledgeWriteJobRepo(conn).start(
                    intent="rename_folder_journal", object_type="folder",
                    object_id=old, payload=plan,
                )
            apply_db = True

        if apply_db:
            # Keep the journal durable while applying the file and DB phases.
            # A rollback leaves the recorded IDs available for the next try.
            with transaction(conn):
                drafts = _draft_folder_updates(conn, old, new, warnings)
                files = _collect_notes(conn, vault_root, old, warnings, set(plan["capture_ids"]), new)
                for capture_id, (path, frontmatter, body, current_folder, original) in files.items():
                    if current_folder == old:
                        frontmatter["folder_path"] = new
                        frontmatter.pop("folder", None)

                        def check_snapshot() -> None:
                            if path.read_bytes() != original:
                                raise RuntimeError(f"capture {capture_id} changed during topic rename")

                        atomic_write_text(path, _render_markdown(frontmatter, body), before_replace=check_snapshot)
                    written.append(capture_id)
                    conn.execute(
                        "UPDATE captures SET updated_at = datetime('now') WHERE id = ?",
                        (capture_id,),
                    )
                    conn.execute(
                        """INSERT INTO capture_metadata
                               (capture_id, folder_path, created_at, updated_at)
                           VALUES (?, ?, datetime('now'), datetime('now'))
                           ON CONFLICT(capture_id) DO UPDATE SET
                               folder_path=excluded.folder_path,
                               updated_at=excluded.updated_at""",
                        (capture_id, new),
                    )
                conn.executemany(
                    "UPDATE capture_drafts SET capture_intent = ?, updated_at = datetime('now') WHERE id = ?",
                    drafts,
                )
                updated_drafts = len(drafts)
                cursor = conn.execute(
                    """UPDATE garden_cards
                       SET source_theme = ?, updated_at = datetime('now')
                       WHERE source_theme = ?""",
                    (new, old),
                )
                updated_garden_cards = cursor.rowcount
                KnowledgeWriteJobRepo(conn).mark_step(job_id, status="pending", step="db_applied")

        rename_stored_folder(vault_root, old, new)
        KnowledgeWriteJobRepo(conn).complete(job_id)
        logger.info(
            "Renamed folder %r -> %r: %d drafts, %d captures, %d garden cards",
            old,
            new,
            updated_drafts,
            len(written),
            updated_garden_cards,
        )

    except Exception as exc:  # noqa: BLE001 - return a structured failure
        errors.append(f"rename failed: {exc}")
        logger.exception("Failed to rename folder %r -> %r", old, new)

    return FolderRenameResult(
        old_folder=old,
        new_folder=new,
        updated_drafts=updated_drafts if not errors else 0,
        updated_captures=len(written),
        updated_garden_cards=updated_garden_cards if not errors else 0,
        updated_metadata=len(written) if not errors else 0,
        errors=errors,
        warnings=warnings,
    )


def _collect_notes(conn, vault_root, old, warnings, planned=None, new=None):
    result = {}
    for row in conn.execute("SELECT id, file_path FROM captures WHERE archived_at IS NULL"):
        capture_id = str(row["id"])
        if planned is not None and capture_id not in planned:
            continue
        file_path = str(row["file_path"] or "")
        try:
            if not file_path:
                raise ValueError("missing file_path")
            path = resolve_inside(vault_root, file_path)
            original = path.read_bytes()
            text = original.decode("utf-8")
            match = _NOTE_FRONTMATTER.match(text)
            frontmatter = (yaml.safe_load(match[1]) or {}) if match else {}
            if not isinstance(frontmatter, dict):
                raise ValueError(f"invalid frontmatter at {file_path}")
            body = text[match.end():] if match else text
        except (OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:
            if planned is not None:
                raise RuntimeError(f"cannot resume capture {capture_id}: {exc}") from exc
            detail = f"file not found at {file_path}" if isinstance(exc, FileNotFoundError) else str(exc)
            warnings.append(f"capture {capture_id}: {detail}")
            continue
        current = normalize_folder(frontmatter.get("folder_path") or frontmatter.get("folder", ""))
        if current == old or (planned is not None and current == new):
            result[capture_id] = (path, frontmatter, body, current, original)
    return result


def _draft_folder_updates(conn, old, new, warnings):
    updates = []
    rows = conn.execute(
        """SELECT id, capture_intent, status FROM capture_drafts
           WHERE COALESCE(committed_capture_id, '') = ''
             AND COALESCE(status, '') NOT IN ('abandoned', 'discarded', 'committed')"""
    ).fetchall()
    for row in rows:
        try:
            intent = json.loads(row["capture_intent"] or "{}")
        except (ValueError, TypeError):
            intent = None
        if not isinstance(intent, dict):
            warnings.append(f"draft {row['id']}: invalid capture_intent")
        elif normalize_folder(intent.get("folder_path", "")) == old:
            if row["status"] == "committing":
                raise RuntimeError(f"capture draft {row['id']} is being saved; retry the topic rename")
            intent["folder_path"] = new
            updates.append((json.dumps(intent, ensure_ascii=False), row["id"]))
    return updates


def _render_markdown(frontmatter: dict[str, Any], body: str) -> str:
    """Render Markdown with YAML frontmatter."""
    yaml_text = yaml.dump(
        frontmatter,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    return f"---\n{yaml_text}\n---\n{body}"
