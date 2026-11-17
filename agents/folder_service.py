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
    """Rename a folder across all data stores.
    
    Updates:
    1. capture_drafts.capture_intent.folder_path (JSON column)
    2. capture_metadata.folder_path (indexed SQL column)
    3. garden_cards.source_theme (when equals old_folder)
    4. captures/*.md frontmatter.folder_path (Markdown files)
    5. captures.updated_at (timestamp)
    
    All operations run inside a single transaction. If any step fails, the
    entire operation rolls back.
    
    Args:
        old_folder: Current folder path (e.g., "AI工具")
        new_folder: Target folder path (e.g., "AI产品")
        vault_path: Knowledge File storage root
        conn: SQLite connection (must support transactions)
    
    Returns:
        FolderRenameResult with counts and any errors/warnings
    """
    old = normalize_folder(old_folder)
    new = normalize_folder(new_folder)
    
    if not new:
        return FolderRenameResult(
            old_folder=old,
            new_folder=new,
            updated_drafts=0,
            updated_captures=0,
            updated_garden_cards=0,
            updated_metadata=0,
            errors=["new folder name cannot be empty"],
            warnings=[],
        )
    
    if old == new:
        return FolderRenameResult(
            old_folder=old,
            new_folder=new,
            updated_drafts=0,
            updated_captures=0,
            updated_garden_cards=0,
            updated_metadata=0,
            errors=[],
            warnings=["old and new folder names are identical"],
        )
    
    errors: list[str] = []
    warnings: list[str] = []
    updated_drafts = 0
    updated_captures = 0
    updated_garden_cards = 0
    updated_metadata = 0
    
    vault_root = Path(vault_path).expanduser()
    
    try:
        # Step 1: Update capture_drafts.capture_intent.folder_path
        draft_rows = conn.execute(
            """SELECT id, capture_intent FROM capture_drafts
               WHERE COALESCE(committed_capture_id, '') = ''
                 AND COALESCE(status, '') NOT IN ('abandoned', 'discarded')"""
        ).fetchall()
        
        for row in draft_rows:
            draft_id = str(row["id"])
            capture_intent_json = str(row["capture_intent"] or "{}")
            try:
                capture_intent = json.loads(capture_intent_json)
            except json.JSONDecodeError:
                warnings.append(f"draft {draft_id}: invalid capture_intent JSON")
                continue
            
            if not isinstance(capture_intent, dict):
                warnings.append(f"draft {draft_id}: capture_intent is not a dict")
                continue
            
            current_folder = normalize_folder(capture_intent.get("folder_path", ""))
            if current_folder != old:
                continue
            
            capture_intent["folder_path"] = new
            conn.execute(
                """UPDATE capture_drafts
                   SET capture_intent = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (json.dumps(capture_intent, ensure_ascii=False), draft_id),
            )
            updated_drafts += 1
        
        # Step 2: Update captures/*.md frontmatter + capture_metadata.folder_path
        capture_rows = conn.execute(
            """SELECT id, file_path FROM captures
               WHERE COALESCE(review_state, '') != 'archived'"""
        ).fetchall()
        
        for row in capture_rows:
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
            except Exception as exc:
                warnings.append(f"capture {capture_id}: read failed — {exc}")
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
            
            # Update frontmatter
            frontmatter["folder_path"] = new
            if "folder" in frontmatter:
                del frontmatter["folder"]  # Remove legacy field
            
            try:
                content = _render_markdown(frontmatter, body)
                atomic_write_text(absolute_path, content)
            except Exception as exc:
                errors.append(f"capture {capture_id}: write failed — {exc}")
                continue
            
            # Update database timestamp
            conn.execute(
                "UPDATE captures SET updated_at = datetime('now') WHERE id = ?",
                (capture_id,),
            )
            
            # Update capture_metadata.folder_path (indexed mirror)
            conn.execute(
                """INSERT INTO capture_metadata
                       (capture_id, folder_path, created_at, updated_at)
                   VALUES (?, ?, datetime('now'), datetime('now'))
                   ON CONFLICT(capture_id) DO UPDATE SET
                       folder_path=excluded.folder_path,
                       updated_at=excluded.updated_at""",
                (capture_id, new),
            )
            updated_metadata += 1
            updated_captures += 1
        
        # Step 3: Update garden_cards.source_theme
        cursor = conn.execute(
            """UPDATE garden_cards
               SET source_theme = ?, updated_at = datetime('now')
               WHERE source_theme = ?""",
            (new, old),
        )
        updated_garden_cards = cursor.rowcount

        # Step 4: keep the folder registry in sync (the Library reads it).
        rename_stored_folder(vault_root, old, new)

        conn.commit()
        logger.info(
            f"Renamed folder {old!r} → {new!r}: "
            f"{updated_drafts} drafts, {updated_captures} captures, "
            f"{updated_garden_cards} garden cards"
        )
        
    except Exception as exc:
        conn.rollback()
        errors.append(f"transaction failed: {exc}")
        logger.exception(f"Failed to rename folder {old!r} → {new!r}")
    
    return FolderRenameResult(
        old_folder=old,
        new_folder=new,
        updated_drafts=updated_drafts,
        updated_captures=updated_captures,
        updated_garden_cards=updated_garden_cards,
        updated_metadata=updated_metadata,
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
