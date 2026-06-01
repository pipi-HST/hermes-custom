#!/usr/bin/env python3
"""Read-only Life Assistant acceptance report."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.life_audit import _load_db_path, run_audit


def run_acceptance(
    *,
    db_path: str | None = None,
    now: str | None = None,
    api_base: str = "http://127.0.0.1:8000/api",
    dashboard_url: str = "http://localhost:3010/life",
    warning_window_hours: int = 6,
) -> dict[str, Any]:
    resolved_db_path = db_path or _load_db_path(ROOT / "config.yaml")
    current = now or datetime.now().astimezone().isoformat()
    checks: list[dict[str, Any]] = []
    sections: dict[str, Any] = {}

    audit = run_audit(
        db_path=resolved_db_path,
        now=current,
        warning_window_hours=warning_window_hours,
    )
    _add_check(
        checks,
        "life_audit",
        _map_audit_status(audit["status"]),
        f"life_audit status={audit['status']}",
    )

    db_section = _database_section(resolved_db_path, checks)
    sections["live_database"] = db_section
    sections["pipeline_coverage"] = _pipeline_section(db_section, audit, checks)
    sections["runtime_audit"] = {
        "status": _map_audit_status(audit["status"]),
        "summary": audit["summary"],
        "checks": audit["checks"],
    }
    sections["api"] = _api_section(api_base, checks)
    sections["dashboard"] = _dashboard_section(dashboard_url, checks)

    return {
        "status": _overall_status(checks),
        "generated_at": datetime.now().astimezone().isoformat(),
        "db_path": resolved_db_path,
        "now": current,
        "sections": sections,
        "checks": checks,
    }


def _database_section(db_path: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        event_counts = _count_by(conn, "life_events", "event_type")
        reminder_counts = _count_by(conn, "life_reminder_logs", "reminder_type")
        daily_reviews = _scalar(conn, "SELECT count(*) FROM life_daily_reviews")
        weekly_reviews = _scalar(conn, "SELECT count(*) FROM life_weekly_reviews")
        decisions = _scalar(
            conn,
            "SELECT count(*) FROM life_long_term_decisions WHERE status IN ('active', 'pending_review_time')",
        )
    finally:
        conn.close()

    _add_check(checks, "sleep_records_exist", _presence(event_counts, "sleep_end"), f"sleep_end={event_counts.get('sleep_end', 0)}")
    _add_check(checks, "meal_records_exist", _presence(event_counts, "meal"), f"meal={event_counts.get('meal', 0)}")
    _add_check(checks, "water_records_exist", _presence(event_counts, "water"), f"water={event_counts.get('water', 0)}")
    _add_check(checks, "exercise_records_exist", _presence(event_counts, "exercise"), f"exercise={event_counts.get('exercise', 0)}")
    _add_check(checks, "sedentary_records_exist", _presence(event_counts, "sedentary_start"), f"sedentary_start={event_counts.get('sedentary_start', 0)}")
    _add_check(checks, "reminder_logs_exist", "passed" if reminder_counts else "warning", f"reminder_types={len(reminder_counts)}")
    _add_check(checks, "daily_reviews_exist", "passed" if daily_reviews else "warning", f"daily_reviews={daily_reviews}")
    _add_check(checks, "weekly_reviews_exist", "passed" if weekly_reviews else "warning", f"weekly_reviews={weekly_reviews}")

    status = "passed" if all(
        [
            event_counts.get("sleep_end", 0),
            event_counts.get("meal", 0),
            event_counts.get("water", 0),
            event_counts.get("exercise", 0),
            reminder_counts,
            daily_reviews,
            weekly_reviews,
        ]
    ) else "warning"
    return {
        "status": status,
        "event_counts": event_counts,
        "reminder_counts": reminder_counts,
        "daily_reviews": daily_reviews,
        "weekly_reviews": weekly_reviews,
        "active_long_term_decisions": decisions,
    }


def _pipeline_section(
    db_section: dict[str, Any],
    audit: dict[str, Any],
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    items = {
        "recording": all(
            db_section["event_counts"].get(name, 0) > 0
            for name in ("sleep_end", "meal", "water", "exercise")
        ),
        "sedentary": db_section["event_counts"].get("sedentary_start", 0) > 0,
        "reminders": bool(db_section["reminder_counts"]),
        "daily_review": db_section["daily_reviews"] > 0,
        "weekly_review": db_section["weekly_reviews"] > 0,
        "runtime_clean": audit["status"] == "ok",
    }
    for name, passed in items.items():
        _add_check(checks, f"pipeline_{name}", "passed" if passed else "warning", str(passed))
    return {
        "status": "passed" if all(items.values()) else "warning",
        "items": items,
    }


def _api_section(api_base: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    if not api_base:
        _add_check(checks, "life_api_reachable", "skipped", "api_base empty")
        return {"status": "skipped", "base": api_base, "endpoints": {}}
    endpoints = {
        "runtime": "/life/runtime",
        "summary": "/life/summary",
        "daily_status": "/life/daily-status",
        "week": "/life/week",
        "reviews": "/life/reviews",
    }
    results: dict[str, Any] = {}
    for name, path in endpoints.items():
        ok, detail = _fetch_ok(f"{api_base.rstrip('/')}{path}")
        results[name] = {"status": "passed" if ok else "failed", "detail": detail}
        _add_check(checks, f"api_{name}", "passed" if ok else "failed", detail)
    return {
        "status": "passed" if all(item["status"] == "passed" for item in results.values()) else "failed",
        "base": api_base,
        "endpoints": results,
    }


def _dashboard_section(dashboard_url: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    if not dashboard_url:
        _add_check(checks, "life_dashboard_reachable", "skipped", "dashboard_url empty")
        return {"status": "skipped", "url": dashboard_url}
    ok, detail = _fetch_ok(dashboard_url)
    _add_check(checks, "life_dashboard_reachable", "passed" if ok else "failed", detail)
    return {"status": "passed" if ok else "failed", "url": dashboard_url, "detail": detail}


def _fetch_ok(url: str) -> tuple[bool, str]:
    try:
        with urlopen(url, timeout=3) as response:
            body = response.read(2048)
            return 200 <= response.status < 400, f"http={response.status}, bytes={len(body)}"
    except (OSError, URLError) as exc:
        return False, str(exc)


def _count_by(conn: sqlite3.Connection, table: str, column: str) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT {column}, count(*) AS cnt FROM {table} GROUP BY {column} ORDER BY {column}"
    ).fetchall()
    return {str(row[column]): int(row["cnt"]) for row in rows}


def _scalar(conn: sqlite3.Connection, query: str) -> int:
    row = conn.execute(query).fetchone()
    return int(row[0] if row else 0)


def _presence(counts: dict[str, int], key: str) -> str:
    return "passed" if counts.get(key, 0) > 0 else "warning"


def _map_audit_status(status: str) -> str:
    if status == "ok":
        return "passed"
    if status == "warning":
        return "warning"
    return "failed"


def _add_check(checks: list[dict[str, Any]], name: str, status: str, detail: str) -> None:
    checks.append({"name": name, "status": status, "detail": detail})


def _overall_status(checks: list[dict[str, Any]]) -> str:
    statuses = {check["status"] for check in checks}
    if "failed" in statuses:
        return "failed"
    if "warning" in statuses:
        return "warning"
    return "passed"


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Life Pipeline Acceptance Report",
        "",
        f"- status: {report['status']}",
        f"- generated_at: {report['generated_at']}",
        f"- db_path: {report['db_path']}",
        "",
        "## Sections",
    ]
    for name, section in report["sections"].items():
        lines.append(f"- {name}: {section.get('status')}")
    lines.extend(["", "## Checks"])
    for check in report["checks"]:
        lines.append(f"- {check['status']} `{check['name']}`: {check['detail']}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only Life Assistant acceptance checks.")
    parser.add_argument("--db-path", default=None, help="SQLite DB path. Defaults to config.yaml.")
    parser.add_argument("--now", default=None, help="Optional ISO timestamp for deterministic checks.")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000/api", help="Set empty to skip API checks.")
    parser.add_argument("--dashboard-url", default="http://localhost:3010/life", help="Set empty to skip dashboard check.")
    parser.add_argument("--warning-window-hours", type=int, default=6)
    parser.add_argument("--output-md", default="", help="Optional markdown report output path.")
    args = parser.parse_args()

    report = run_acceptance(
        db_path=args.db_path,
        now=args.now,
        api_base=args.api_base,
        dashboard_url=args.dashboard_url,
        warning_window_hours=args.warning_window_hours,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output_md:
        Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_md).write_text(_markdown_report(report), encoding="utf-8")
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
