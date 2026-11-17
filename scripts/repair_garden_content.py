"""Audit and repair the Knowledge Card ``content_markdown`` cache column.

``garden_cards.payload_json.content_markdown`` is the single source of truth
for a card's body.  The ``garden_cards.content_markdown`` column is a derived
cache for legacy readers; it must never win over the payload.

This script is dry-run by default: it lists cards whose column differs from the
payload body.  ``--apply`` copies the payload body into the column for those
cards (it never rewrites ``payload_json``) and prints the number of rows
changed.  Cards whose payload lacks ``content_markdown`` are reported and left
alone, because syncing them would erase the only body they have.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.garden_card_payload import parse_payload_json
from db.concurrency import transaction
from db.schema import init_db


def repair_garden_content(
    db_path: str | Path,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Return a report and optionally sync the column to the payload."""
    resolved_db = str(Path(db_path).expanduser())
    conn = init_db(resolved_db)
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """SELECT id, title, content_markdown, payload_json
                   FROM garden_cards
                   ORDER BY id"""
            ).fetchall()
        ]
        mismatches: list[dict[str, str]] = []
        desired: list[tuple[str, str]] = []
        missing_payload: list[dict[str, str]] = []
        for card in rows:
            payload_text = str(
                parse_payload_json(card.get("payload_json")).get("content_markdown") or ""
            )
            column_text = str(card.get("content_markdown") or "")
            if not payload_text:
                if column_text:
                    missing_payload.append(
                        {
                            "card_id": str(card.get("id") or ""),
                            "title": str(card.get("title") or ""),
                            "reason": "payload has no content_markdown; column left untouched",
                        }
                    )
                continue
            if column_text != payload_text:
                mismatches.append(
                    {
                        "card_id": str(card.get("id") or ""),
                        "title": str(card.get("title") or ""),
                        "column_length": str(len(column_text)),
                        "payload_length": str(len(payload_text)),
                    }
                )
                desired.append((str(card.get("id") or ""), payload_text))

        if apply and desired:
            with transaction(conn):
                for card_id, payload_text in desired:
                    conn.execute(
                        "UPDATE garden_cards SET content_markdown = ? WHERE id = ?",
                        (payload_text, card_id),
                    )

        return {
            "mode": "apply" if apply else "dry-run",
            "db_path": resolved_db,
            "cards_scanned": len(rows),
            "mismatches": mismatches,
            "missing_payload": missing_payload,
            "changed": len(mismatches) if apply else 0,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--db", default="")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="copy payload content_markdown into the column (default: dry-run)",
    )
    args = parser.parse_args(argv)

    db_path = (args.db or "").strip()
    if not db_path:
        from agents.capture_agent import load_config

        db_path = load_config(args.config).db_path
    report = repair_garden_content(db_path, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
