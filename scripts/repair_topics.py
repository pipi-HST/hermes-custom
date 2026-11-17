"""Audit and repair Topic/Folder mirrors and Knowledge Card source topics.

The Markdown frontmatter is the source of truth for a Capture's folder
(``folder_path``).  Two derived stores can drift:

* ``capture_metadata.folder_path`` — the SQL mirror used by list filters;
* ``garden_cards.source_theme`` — the card's topic label.

This script is dry-run by default.  ``--apply`` writes only those two derived
stores; it never rewrites Capture Markdown and never changes a card's content.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from bridge.folder_store import normalize_folder
from db.concurrency import transaction
from db.schema import init_db


def _read_frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _capture_folder(frontmatter: dict[str, Any]) -> str:
    return normalize_folder(frontmatter.get("folder_path") or frontmatter.get("folder"))


def _capture_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def repair_topics(
    db_path: str | Path,
    vault_path: str | Path,
    *,
    apply: bool = False,
    fix_mismatched: bool = False,
) -> dict[str, Any]:
    """Return a report and optionally repair derived topic stores."""
    vault = Path(vault_path).expanduser()
    captures_dir = vault / "captures"
    conn = init_db(str(Path(db_path).expanduser()))

    captures: dict[str, dict[str, Any]] = {}
    missing_captures: list[str] = []
    missing_ids: list[str] = []
    for path in sorted(captures_dir.rglob("*.md")) if captures_dir.is_dir() else []:
        relative = path.relative_to(vault).as_posix()
        frontmatter = _read_frontmatter(path)
        capture_id = str(frontmatter.get("id") or "").strip()
        if not capture_id:
            row = conn.execute(
                "SELECT id FROM captures WHERE file_path = ?", (relative,)
            ).fetchone()
            capture_id = str(row["id"] or "") if row else ""
        if not capture_id:
            missing_ids.append(relative)
            continue
        row = conn.execute(
            "SELECT id, file_path FROM captures WHERE id = ?", (capture_id,)
        ).fetchone()
        if row is None:
            missing_captures.append(relative)
            continue
        captures[capture_id] = {
            "id": capture_id,
            "file_path": relative,
            "folder": _capture_folder(frontmatter),
        }

    metadata_by_id = {
        str(row["capture_id"]): str(row["folder_path"] or "")
        for row in conn.execute(
            "SELECT capture_id, folder_path FROM capture_metadata"
        ).fetchall()
    }
    metadata_updates = []
    for capture_id, data in captures.items():
        current_raw = metadata_by_id.get(capture_id)
        current = normalize_folder(current_raw)
        desired = data["folder"]
        if current_raw is None or current != desired:
            metadata_updates.append(
                {
                    "capture_id": capture_id,
                    "file_path": data["file_path"],
                    "from": current_raw or "",
                    "to": desired,
                }
            )

    card_rows = [
        dict(row)
        for row in conn.execute(
            "SELECT id, title, source_theme, capture_ids FROM garden_cards"
        ).fetchall()
    ]
    card_updates = []
    card_mismatches = []
    for card in card_rows:
        ids = _capture_ids(card.get("capture_ids"))
        folders = [
            captures[capture_id]["folder"]
            for capture_id in ids
            if capture_id in captures
        ]
        non_inbox = [folder for folder in folders if folder != "Inbox"]
        if not non_inbox:
            continue
        # Follow the card's own capture order; ties keep the first topic.
        counts = Counter(non_inbox)
        top = max(counts.values())
        suggested = next(folder for folder in non_inbox if counts[folder] == top)
        current = str(card.get("source_theme") or "").strip()
        current_folder = normalize_folder(current)
        needs_update = current_folder != suggested and (
            current_folder == "Inbox" or not current or fix_mismatched
        )
        if needs_update:
            card_updates.append(
                {
                    "card_id": card["id"],
                    "title": card.get("title", ""),
                    "from": current,
                    "to": suggested,
                }
            )
        elif current_folder != suggested:
            card_mismatches.append(
                {
                    "card_id": card["id"],
                    "title": card.get("title", ""),
                    "current": current,
                    "suggested": suggested,
                }
            )

    if apply and (metadata_updates or card_updates):
        with transaction(conn):
            for item in metadata_updates:
                conn.execute(
                    """INSERT INTO capture_metadata
                           (capture_id, folder_path, created_at, updated_at)
                       VALUES (?, ?, datetime('now'), datetime('now'))
                       ON CONFLICT(capture_id) DO UPDATE SET
                           folder_path=excluded.folder_path,
                           updated_at=excluded.updated_at""",
                    (item["capture_id"], item["to"]),
                )
            for item in card_updates:
                conn.execute(
                    "UPDATE garden_cards SET source_theme = ?, updated_at = datetime('now') WHERE id = ?",
                    (item["to"], item["card_id"]),
                )

    report = {
        "mode": "apply" if apply else "dry-run",
        "db_path": str(Path(db_path).expanduser()),
        "vault_path": str(vault),
        "captures_scanned": len(captures),
        "metadata_updates": metadata_updates,
        "card_updates": card_updates,
        "card_mismatches": card_mismatches,
        "missing_captures": missing_captures,
        "missing_ids": missing_ids,
        "applied": bool(apply and (metadata_updates or card_updates)),
    }
    conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--db", default="")
    parser.add_argument("--vault", default="")
    parser.add_argument("--apply", action="store_true", help="write the repairs (default: dry-run)")
    parser.add_argument(
        "--fix-mismatched",
        action="store_true",
        help="also overwrite a non-Inbox card topic when it differs from its captures",
    )
    args = parser.parse_args(argv)

    from agents.capture_agent import load_config

    config = load_config(args.config)
    db_path = args.db or config.db_path
    vault_path = args.vault or config.vault_path
    report = repair_topics(
        db_path,
        vault_path,
        apply=args.apply,
        fix_mismatched=args.fix_mismatched,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
