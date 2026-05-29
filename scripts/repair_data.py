"""Audit and safely repair local Hermes capture data."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.markdown_writer import make_note_filename, read_note
from db.schema import init_db


OPEN_DRAFT_STATUSES = ("raw_extracted", "draft_ready", "revision_requested")


def repair_data(
    db_path: str | Path,
    vault_path: str | Path,
    *,
    apply: bool = False,
    backup_dir: str | Path | None = None,
    report_dir: str | Path | None = None,
    rename_readable: bool = False,
) -> dict[str, Any]:
    """Audit and optionally repair duplicate captures and duplicate open drafts."""
    db = Path(db_path).expanduser()
    vault = Path(vault_path).expanduser()
    backup_root = Path(backup_dir).expanduser() if backup_dir else db.parent / "backups"
    report_root = Path(report_dir).expanduser() if report_dir else db.parent / "repair_reports"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    backup_path = None
    if apply:
        backup_path = _backup_db(db, backup_root, timestamp)

    conn = init_db(str(db))
    try:
        vault_index = _index_vault(vault)
        before = _summary(conn, vault)
        actions: list[dict[str, Any]] = []
        missing_reports: list[dict[str, Any]] = []
        missing_deletions: list[dict[str, Any]] = []

        actions.extend(_duplicate_capture_actions(conn, vault, vault_index))
        _extend_missing_file_actions(
            conn,
            vault,
            vault_index,
            actions,
            missing_reports,
            missing_deletions,
        )
        actions.extend(_duplicate_open_draft_actions(conn))
        if rename_readable:
            actions.extend(_readable_rename_actions(conn, vault, actions))

        if apply:
            _apply_actions(conn, vault, actions)

        after = _summary(conn, vault)
        result: dict[str, Any] = {
            "dry_run": not apply,
            "db_path": str(db),
            "vault_path": str(vault),
            "backup_path": str(backup_path) if backup_path else "",
            "report_path": "",
            "before": before,
            "after": after,
            "planned_actions": actions,
            "missing_file_reports": missing_reports,
            "missing_file_deletions": missing_deletions,
        }
        result["report_path"] = str(_write_report(report_root, timestamp, result))
        return result
    finally:
        conn.close()


def _backup_db(db_path: Path, backup_dir: Path, timestamp: str) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{db_path.stem}-{timestamp}.db"
    source = sqlite3.connect(str(db_path))
    try:
        dest = sqlite3.connect(str(backup_path))
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    return backup_path


def _write_report(report_dir: Path, timestamp: str, result: dict[str, Any]) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"repair-{timestamp}.json"
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report_path


def _index_vault(vault_path: Path) -> dict[str, dict[str, Any]]:
    ids: dict[str, list[str]] = {}
    paths: dict[str, str] = {}
    if not vault_path.exists():
        return {"ids": ids, "paths": paths}

    for note_path in vault_path.rglob("*.md"):
        rel_path = note_path.relative_to(vault_path).as_posix()
        note = read_note(vault_path, rel_path)
        if not note:
            continue
        frontmatter, _body = note
        capture_id = str(frontmatter.get("id") or "")
        if not capture_id:
            continue
        ids.setdefault(capture_id, []).append(rel_path)
        paths[rel_path] = capture_id
    return {"ids": ids, "paths": paths}


def _summary(conn: sqlite3.Connection, vault_path: Path) -> dict[str, int]:
    return {
        "captures": _count(conn, "SELECT count(*) FROM captures"),
        "capture_drafts": _count(conn, "SELECT count(*) FROM capture_drafts"),
        "duplicate_capture_paths": _count(
            conn,
            """SELECT count(*) FROM (
                   SELECT file_path FROM captures
                   WHERE file_path != ''
                   GROUP BY file_path HAVING count(*) > 1
               )""",
        ),
        "open_draft_duplicate_groups": _count_open_draft_duplicate_groups(conn),
        "missing_capture_paths": _count_missing_capture_paths(conn, vault_path),
    }


def _count(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def _count_open_draft_duplicate_groups(conn: sqlite3.Connection) -> int:
    placeholders = ",".join("?" for _ in OPEN_DRAFT_STATUSES)
    sql = f"""SELECT count(*) FROM (
                 SELECT status, source, source_type, title, raw_text, draft_text, instruction
                 FROM capture_drafts
                 WHERE status IN ({placeholders})
                 GROUP BY status, source, source_type, title, raw_text, draft_text, instruction
                 HAVING count(*) > 1
             )"""
    return int(conn.execute(sql, OPEN_DRAFT_STATUSES).fetchone()[0])


def _count_missing_capture_paths(conn: sqlite3.Connection, vault_path: Path) -> int:
    rows = conn.execute("SELECT file_path, raw_path FROM captures").fetchall()
    missing = 0
    for row in rows:
        file_path = row["file_path"]
        raw_path = row["raw_path"]
        if (file_path and not (vault_path / file_path).exists()) or (
            raw_path and not (vault_path / raw_path).exists()
        ):
            missing += 1
    return missing


def _duplicate_capture_actions(
    conn: sqlite3.Connection,
    vault_path: Path,
    vault_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT rowid, * FROM captures WHERE file_path != '' ORDER BY rowid"
    ).fetchall()
    by_path: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_path.setdefault(row["file_path"], []).append(row)

    actions: list[dict[str, Any]] = []
    path_ids = vault_index["paths"]
    for file_path, group in by_path.items():
        if len(group) < 2:
            continue
        canonical = _canonical_capture(group, vault_path, path_ids.get(file_path))
        for row in group:
            if row["id"] == canonical["id"]:
                continue
            actions.append(
                {
                    "type": "delete_duplicate_capture",
                    "file_path": file_path,
                    "delete_id": row["id"],
                    "canonical_id": canonical["id"],
                }
            )
    return actions


def _canonical_capture(
    rows: list[sqlite3.Row],
    vault_path: Path,
    frontmatter_id: str | None,
) -> sqlite3.Row:
    if frontmatter_id:
        for row in rows:
            if row["id"] == frontmatter_id:
                return row

    return sorted(
        rows,
        key=lambda row: (
            (vault_path / row["file_path"]).exists(),
            _sortable_time(row["created_at"]),
            int(row["rowid"]),
        ),
    )[-1]


def _extend_missing_file_actions(
    conn: sqlite3.Connection,
    vault_path: Path,
    vault_index: dict[str, dict[str, Any]],
    actions: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    deletions: list[dict[str, Any]],
) -> None:
    duplicate_deletes = {
        action["delete_id"]
        for action in actions
        if action["type"] == "delete_duplicate_capture"
    }
    rows = conn.execute("SELECT rowid, * FROM captures ORDER BY rowid").fetchall()
    ids = vault_index["ids"]
    for row in rows:
        if row["id"] in duplicate_deletes:
            continue
        file_path = row["file_path"]
        raw_path = row["raw_path"]
        capture_exists = bool(file_path and (vault_path / file_path).exists())
        raw_exists = bool(not raw_path or (vault_path / raw_path).exists())
        if capture_exists and raw_exists:
            continue

        entry = {
            "type": "missing_capture_file",
            "capture_id": row["id"],
            "file_path": file_path,
            "raw_path": raw_path,
        }
        if capture_exists and raw_path and not raw_exists:
            actions.append(
                {
                    "type": "clear_missing_raw_path",
                    "capture_id": row["id"],
                    "file_path": file_path,
                    "raw_path": raw_path,
                }
            )
            reports.append(entry)
            continue

        if row["id"] in ids:
            reports.append(entry)
            continue

        delete_action = {
            "type": "delete_missing_capture",
            "capture_id": row["id"],
            "file_path": file_path,
            "raw_path": raw_path,
        }
        actions.append(delete_action)
        deletions.append(delete_action)


def _capture_paths_missing(vault_path: Path, file_path: str, raw_path: str) -> bool:
    paths = [path for path in (file_path, raw_path) if path]
    if not paths:
        return True
    return any(not (vault_path / path).exists() for path in paths)


def _duplicate_open_draft_actions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in OPEN_DRAFT_STATUSES)
    rows = conn.execute(
        f"""SELECT rowid, * FROM capture_drafts
            WHERE status IN ({placeholders})
            ORDER BY rowid""",
        OPEN_DRAFT_STATUSES,
    ).fetchall()
    groups: dict[tuple[Any, ...], list[sqlite3.Row]] = {}
    for row in rows:
        key = (
            row["status"],
            row["source"],
            row["source_type"],
            row["title"],
            row["raw_text"],
            row["draft_text"],
            row["instruction"],
        )
        groups.setdefault(key, []).append(row)

    actions: list[dict[str, Any]] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        keep = sorted(
            group,
            key=lambda row: (
                _sortable_time(row["created_at"]),
                _sortable_time(row["updated_at"]),
                int(row["rowid"]),
            ),
        )[-1]
        for row in group:
            if row["id"] == keep["id"]:
                continue
            actions.append(
                {
                    "type": "discard_duplicate_open_draft",
                    "discard_id": row["id"],
                    "keep_id": keep["id"],
                    "status": row["status"],
                    "title": row["title"],
                }
            )
    return actions


def _readable_rename_actions(
    conn: sqlite3.Connection,
    vault_path: Path,
    actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    deleted_ids = {
        action.get("delete_id") or action.get("capture_id")
        for action in actions
        if action["type"] in {"delete_duplicate_capture", "delete_missing_capture"}
    }
    cleared_raw_ids = {
        action["capture_id"]
        for action in actions
        if action["type"] == "clear_missing_raw_path"
    }
    rows = conn.execute("SELECT * FROM captures ORDER BY created_at, id").fetchall()
    rename_actions: list[dict[str, Any]] = []
    for row in rows:
        capture_id = row["id"]
        if capture_id in deleted_ids:
            continue
        file_path = row["file_path"]
        if not file_path or not (vault_path / file_path).exists():
            continue
        note = read_note(vault_path, file_path)
        if not note:
            continue
        frontmatter, _body = note
        desired_capture = _readable_path(
            "captures",
            capture_id,
            frontmatter.get("title") or "",
            frontmatter.get("date") or row["created_at"],
        )
        raw_path = row["raw_path"] if capture_id not in cleared_raw_ids else ""
        desired_raw = ""
        if raw_path and (vault_path / raw_path).exists():
            desired_raw = _readable_path(
                "_archive/raw",
                capture_id,
                frontmatter.get("title") or "",
                frontmatter.get("date") or row["created_at"],
            )
        if desired_capture == file_path and (not raw_path or desired_raw == raw_path):
            continue
        rename_actions.append(
            {
                "type": "rename_capture_file",
                "capture_id": capture_id,
                "old_file_path": file_path,
                "new_file_path": desired_capture,
                "old_raw_path": raw_path,
                "new_raw_path": desired_raw,
            }
        )
    return rename_actions


def _readable_path(stage: str, capture_id: str, title: str, date_value: str) -> str:
    return f"{stage}/{make_note_filename(title, date_value, capture_id)}"


def _apply_actions(
    conn: sqlite3.Connection, vault_path: Path, actions: list[dict[str, Any]]
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        for action in actions:
            if action["type"] == "delete_duplicate_capture":
                conn.execute(
                    """UPDATE capture_drafts
                       SET committed_capture_id = ?, updated_at = ?
                       WHERE committed_capture_id = ?""",
                    (action["canonical_id"], now, action["delete_id"]),
                )
                conn.execute(
                    "DELETE FROM captures WHERE id = ?",
                    (action["delete_id"],),
                )
            elif action["type"] == "delete_missing_capture":
                conn.execute(
                    """UPDATE capture_drafts
                       SET committed_capture_id = '', updated_at = ?
                       WHERE committed_capture_id = ?""",
                    (now, action["capture_id"]),
                )
                conn.execute(
                    "DELETE FROM captures WHERE id = ?",
                    (action["capture_id"],),
                )
            elif action["type"] == "clear_missing_raw_path":
                conn.execute(
                    """UPDATE captures
                       SET raw_path = '', updated_at = ?
                       WHERE id = ?""",
                    (now, action["capture_id"]),
                )
            elif action["type"] == "discard_duplicate_open_draft":
                conn.execute(
                    """UPDATE capture_drafts
                       SET status = 'discarded', updated_at = ?
                       WHERE id = ?""",
                    (now, action["discard_id"]),
                )
            elif action["type"] == "rename_capture_file":
                old_file = vault_path / action["old_file_path"]
                new_file = vault_path / action["new_file_path"]
                if old_file.exists() and old_file != new_file:
                    new_file.parent.mkdir(parents=True, exist_ok=True)
                    old_file.rename(new_file)

                old_raw_path = action.get("old_raw_path") or ""
                new_raw_path = action.get("new_raw_path") or ""
                if old_raw_path and new_raw_path:
                    old_raw = vault_path / old_raw_path
                    new_raw = vault_path / new_raw_path
                    if old_raw.exists() and old_raw != new_raw:
                        new_raw.parent.mkdir(parents=True, exist_ok=True)
                        old_raw.rename(new_raw)

                conn.execute(
                    """UPDATE captures
                       SET file_path = ?, raw_path = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        action["new_file_path"],
                        new_raw_path or old_raw_path,
                        now,
                        action["capture_id"],
                    ),
                )


def _sortable_time(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("T", " ")[:19]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit and repair local Hermes capture data."
    )
    parser.add_argument("--db", default="data/captures.db")
    parser.add_argument("--vault", default="~/Obsidian/hermes-vault")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rename-readable", action="store_true")
    args = parser.parse_args()

    result = repair_data(
        args.db,
        args.vault,
        apply=args.apply,
        rename_readable=args.rename_readable,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
