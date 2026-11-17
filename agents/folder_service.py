"""Folder management domain service — authoritative folder rename logic.

This module provides the single source of truth for folder operations across
all data stores (SQLite + Markdown frontmatter).  All folder renames must go
through this service to maintain consistency.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agents.markdown_writer import read_note
from bridge.atomic_io import resolve_inside, atomic_write_text
from bridge.folder_store import normalize_folder, rename_stored_folder
from db.concurrency import transaction

logger = logging.getLogger(__name__)


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
    """Rename a folder across all data stores in one unit of work.

    Order: collect -> write files -> one DB transaction -> folder registry.
    The SQLite connection is autocommit, so the DB phase must be wrapped in
    ``transaction()``; ``conn.commit()/rollback()`` alone are no-ops.
    """
    old = normalize_folder(old_folder)
    new = normalize_folder(new_folder)

    if not new:
        return FolderRenameResult(old, new, 0, 0, 0, 0, ["new folder name cannot be empty"], [])
    if old == new:
        return FolderRenameResult(old, new, 0, 0, 0, 0, [], ["old and new folder names are identical"])

    errors: list[str] = []
    warnings: list[str] = []
    draft_updates: list[tuple[str, str]] = []
    file_updates: list[dict[str, Any]] = []
    updated_garden_cards = 0
    vault_root = Path(vault_path).expanduser()

    try:
        # Phase 1: collect draft intents.
        for row in conn.execute(
            """SELECT id, capture_intent FROM capture_drafts
               WHERE COALESCE(committed_capture_id, '') = ''
                 AND COALESCE(status, '') NOT IN ('abandoned', 'discarded')"""
        ).fetchall():
            draft_id = str(row["id"])
            try:
                intent = json.loads(str(row["capture_intent"] or "{}"))
            except json.JSONDecodeError:
                warnings.append(f"draft {draft_id}: invalid capture_intent JSON")
                continue
            if not isinstance(intent, dict):
                warnings.append(f"draft {draft_id}: capture_intent is not a dict")
                continue
            if normalize_folder(intent.get("folder_path", "")) != old:
                continue
            intent["folder_path"] = new
            draft_updates.append((json.dumps(intent, ensure_ascii=False), draft_id))

        # Phase 2: collect capture files whose frontmatter matches.
        for row in conn.execute(
            "SELECT id, file_path FROM captures WHERE archived_at IS NULL"
        ).fetchall():
            capture_id = str(row["id"])
            file_path = str(row["file_path"] or "")
            if not file_path:
                warnings.append(f"capture {capture_id}: missing file_path")
                continue
            try:
                absolute_path = resolve_inside(vault_root, file_path)
            except ValueError:
                warnings.append(f"capture {capture_id}: invalid path {file_path}")
                continue
            if not absolute_path.exists():
                warnings.append(f"capture {capture_id}: file not found at {file_path}")
                continue
            try:
                note_result = read_note(vault_root, file_path)
            except Exception as exc:  # noqa: BLE001 - one bad note must not stop the rest
                warnings.append(f"capture {capture_id}: read failed - {exc}")
                continue
            if note_result is None:
                warnings.append(f"capture {capture_id}: could not read {file_path}")
                continue
            frontmatter, body = note_result
            current_folder = normalize_folder(
                frontmatter.get("folder_path") or frontmatter.get("folder", "")
            )
            if current_folder != old:
                continue
            frontmatter["folder_path"] = new
            frontmatter.pop("folder", None)  # drop the legacy key
            file_updates.append(
                {
                    "capture_id": capture_id,
                    "path": absolute_path,
                    "content": _render_markdown(frontmatter, body),
                }
            )

        # Phase 3: write files. A failed write drops that row from the DB phase
        # so the mirror never claims a folder the note does not have.
        written: list[dict[str, Any]] = []
        for item in file_updates:
            try:
                atomic_write_text(item["path"], item["content"])
                written.append(item)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"capture {item['capture_id']}: write failed - {exc}")

        # Phase 4: one short DB transaction for every derived index.
        if draft_updates or written:
            with transaction(conn):
                for intent_json, draft_id in draft_updates:
                    conn.execute(
                        "UPDATE capture_drafts SET capture_intent = ?, updated_at = datetime('now') WHERE id = ?",
                        (intent_json, draft_id),
                    )
                for item in written:
                    conn.execute(
                        "UPDATE captures SET updated_at = datetime('now') WHERE id = ?",
                        (item["capture_id"],),
                    )
                    conn.execute(
                        """INSERT INTO capture_metadata
                               (capture_id, folder_path, created_at, updated_at)
                           VALUES (?, ?, datetime('now'), datetime('now'))
                           ON CONFLICT(capture_id) DO UPDATE SET
                               folder_path=excluded.folder_path,
                               updated_at=excluded.updated_at""",
                        (item["capture_id"], new),
                    )
                cursor = conn.execute(
                    """UPDATE garden_cards
                       SET source_theme = ?, updated_at = datetime('now')
                       WHERE source_theme = ?""",
                    (new, old),
                )
                updated_garden_cards = cursor.rowcount

        # Phase 5: registry after the DB commit.
        rename_stored_folder(vault_root, old, new)
        logger.info(
            "Renamed folder %r -> %r: %d drafts, %d captures, %d garden cards",
            old,
            new,
            len(draft_updates),
            len(written),
            updated_garden_cards,
        )

    except Exception as exc:  # noqa: BLE001 - return a structured failure
        errors.append(f"rename failed: {exc}")
        logger.exception("Failed to rename folder %r -> %r", old, new)

    return FolderRenameResult(
        old_folder=old,
        new_folder=new,
        updated_drafts=len(draft_updates),
        updated_captures=len(written),
        updated_garden_cards=updated_garden_cards,
        updated_metadata=len(written),
        errors=errors,
        warnings=warnings,
    )


def _render_markdown(frontmatter: dict[str, Any], body: str) -> str:
    """Render Markdown with YAML frontmatter."""
    yaml_text = yaml.dump(
        frontmatter,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    return f"---\n{yaml_text}\n---\n\n{body}"
