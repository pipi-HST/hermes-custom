"""Dry-run and apply reset for local Hermes Knowledge personal data."""

from __future__ import annotations

import argparse
import json
import plistlib
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

CONFIRMATION_PHRASE = "RESET HERMES LOCAL DATA"

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]


def _default_db_path() -> Path:
    """Read the configured DB path instead of assuming a macOS checkout."""
    config_path = DEFAULT_REPO_ROOT / "config.yaml"
    try:
        import yaml

        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        configured = (data.get("database") or {}).get("path")
        if configured:
            return Path(str(configured)).expanduser()
    except (OSError, ValueError, ImportError):
        pass
    return DEFAULT_REPO_ROOT / "data" / "captures.db"


DEFAULT_DB_PATH = _default_db_path()
DEFAULT_LEGACY_STORE = Path.home() / "Obsidian" / "hermes-vault"
DEFAULT_NEW_STORAGE_ROOT = Path.home() / "Hermes" / "knowledge"
DEFAULT_CRON_JOBS_PATH = Path.home() / ".hermes" / "cron" / "jobs.json"
DEFAULT_LIFE_DB_PATH = Path.home() / "hermes-life" / "data" / "life.db"
WATCHER_LABEL = "com.pipi.hermes-vault-watcher"

KNOWLEDGE_TABLES = [
    "agent_run_events",
    "agent_run_objects",
    "agent_runs",
    "knowledge_edges",
    "knowledge_write_jobs",
    "change_log",
    "capture_version_backups",
    "reading_evidence",
    "reading_turns",
    "reading_drafts",
    "reading_sessions",
    "review_items",
    "capture_embeddings",
    "capture_metadata",
    "pending_questions",
    "garden_drafts",
    "garden_cards",
    "capture_drafts",
    "captures",
    "capture_search_fts",
    "capture_search_fts_map",
]

NEW_STORAGE_DIRS = [
    "captures",
    "_archive/raw",
    "attachments",
]


def build_reset_plan(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    legacy_store: str | Path = DEFAULT_LEGACY_STORE,
    new_storage_root: str | Path = DEFAULT_NEW_STORAGE_ROOT,
    repo_root: str | Path = DEFAULT_REPO_ROOT,
) -> dict[str, Any]:
    db = Path(db_path).expanduser()
    legacy = Path(legacy_store).expanduser()
    storage = Path(new_storage_root).expanduser()
    repo = Path(repo_root).expanduser()

    tables = _table_counts(db)
    file_targets = _file_targets(legacy, repo)
    return {
        "dry_run": True,
        "db_path": str(db),
        "tables": tables,
        "legacy_knowledge_store": _path_report(legacy),
        "content_backup_targets": file_targets,
        "new_storage_root": str(storage),
        "new_storage_dirs": [
            {"path": str(storage / rel_path), "exists": (storage / rel_path).exists()}
            for rel_path in NEW_STORAGE_DIRS
        ],
        "new_storage_files": _storage_files(storage),
        "preserved_paths": [
            str(repo / "config.yaml"),
            str(repo / "data" / "rag.log"),
            str(DEFAULT_LIFE_DB_PATH),
        ],
        "automation_preflight": _automation_preflight(
            legacy_store=legacy,
            new_storage_root=storage,
            repo_root=repo,
        ),
        "confirmation_phrase": CONFIRMATION_PHRASE,
    }


def reset_local_data(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    legacy_store: str | Path = DEFAULT_LEGACY_STORE,
    new_storage_root: str | Path = DEFAULT_NEW_STORAGE_ROOT,
    repo_root: str | Path = DEFAULT_REPO_ROOT,
    apply: bool = False,
    confirmation: str = "",
) -> dict[str, Any]:
    plan = build_reset_plan(
        db_path=db_path,
        legacy_store=legacy_store,
        new_storage_root=new_storage_root,
        repo_root=repo_root,
    )
    if not apply:
        return plan
    _require_confirmation(confirmation)
    _require_safe_to_apply(plan)
    _require_safe_delete_targets(
        Path(legacy_store).expanduser(),
        Path(repo_root).expanduser(),
    )

    db = Path(db_path).expanduser()
    storage = Path(new_storage_root).expanduser()
    for target in _allowed_delete_targets(
        Path(legacy_store).expanduser(),
        Path(repo_root).expanduser(),
    ):
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()

    if db.exists():
        _delete_table_rows(db, plan["tables"])

    _clear_storage_root(storage)
    for rel_path in NEW_STORAGE_DIRS:
        (storage / rel_path).mkdir(parents=True, exist_ok=True)

    after = build_reset_plan(
        db_path=db_path,
        legacy_store=legacy_store,
        new_storage_root=new_storage_root,
        repo_root=repo_root,
    )
    after["dry_run"] = False
    after["applied"] = True
    return after


def verify_empty_reset(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    legacy_store: str | Path = DEFAULT_LEGACY_STORE,
    new_storage_root: str | Path = DEFAULT_NEW_STORAGE_ROOT,
    repo_root: str | Path = DEFAULT_REPO_ROOT,
    life_db_path: str | Path = DEFAULT_LIFE_DB_PATH,
) -> dict[str, Any]:
    db = Path(db_path).expanduser()
    legacy = Path(legacy_store).expanduser()
    storage = Path(new_storage_root).expanduser()
    repo = Path(repo_root).expanduser()
    life_db = Path(life_db_path).expanduser()

    failures: list[str] = []
    table_reports = _table_counts(db)
    non_empty_tables = [
        item
        for item in table_reports
        if item.get("category") in {"knowledge", "legacy_life_residual"}
        and int(item.get("rows") or 0) != 0
    ]
    if not db.exists():
        failures.append(f"database missing: {db}")
    elif non_empty_tables:
        failures.append(
            "non-empty reset tables: "
            + ", ".join(f"{item['name']}={item['rows']}" for item in non_empty_tables)
        )

    schema_checks = _schema_checks(db)
    if not schema_checks["queryable"]:
        failures.append("database schema is not queryable")

    if legacy.exists():
        failures.append(f"legacy store still exists: {legacy}")

    existing_backup_targets = [
        report["path"]
        for report in _file_targets(legacy, repo)
        if report.get("exists")
    ]
    if existing_backup_targets:
        failures.append("content backup targets still exist: " + ", ".join(existing_backup_targets))

    storage_dirs = [
        {"path": str(storage / rel_path), "exists": (storage / rel_path).is_dir()}
        for rel_path in NEW_STORAGE_DIRS
    ]
    missing_dirs = [item["path"] for item in storage_dirs if not item["exists"]]
    if missing_dirs:
        failures.append("new storage directories missing: " + ", ".join(missing_dirs))

    storage_files = _storage_files(storage)
    if storage_files:
        failures.append("new storage is not empty: " + ", ".join(storage_files[:20]))

    preserved_paths = {
        "config_yaml": str(repo / "config.yaml"),
        "rag_log": str(repo / "data" / "rag.log"),
        "life_db": str(life_db),
    }
    if not (repo / "config.yaml").exists():
        failures.append(f"config.yaml missing: {repo / 'config.yaml'}")
    if not life_db.exists():
        failures.append(f"independent Life DB missing: {life_db}")

    return {
        "ok": not failures,
        "failures": failures,
        "db_path": str(db),
        "tables": table_reports,
        "schema": schema_checks,
        "legacy_knowledge_store": _path_report(legacy),
        "content_backup_targets": _file_targets(legacy, repo),
        "new_storage_root": str(storage),
        "new_storage_dirs": storage_dirs,
        "new_storage_files": storage_files,
        "preserved_paths": preserved_paths,
    }


def _require_confirmation(value: str) -> None:
    if value != CONFIRMATION_PHRASE:
        raise ValueError(
            f"--apply requires confirmation phrase: {CONFIRMATION_PHRASE!r}"
        )


def _require_safe_to_apply(plan: dict[str, Any]) -> None:
    preflight = plan.get("automation_preflight") or {}
    blockers = [str(item) for item in preflight.get("blockers") or []]
    if blockers:
        raise ValueError(
            "Unsafe to apply reset while automation is active: "
            + "; ".join(blockers)
        )


def _require_safe_delete_targets(legacy_store: Path, repo_root: Path) -> None:
    """Reject roots where ``rmtree`` would destroy the checkout or home dir."""
    home = Path.home().resolve()
    legacy = legacy_store.resolve()
    repo = repo_root.resolve()
    for label, target in (("legacy store", legacy), ("repo root", repo)):
        if target == Path(target.anchor) or target == home:
            raise ValueError(f"Refusing to delete unsafe {label}: {target}")
    if legacy == repo:
        raise ValueError(f"Refusing to delete repo root as legacy store: {legacy}")


def _table_counts(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    try:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        tables = [
            {"name": table, "rows": _count_rows(conn, table), "category": "knowledge"}
            for table in KNOWLEDGE_TABLES
            if table in existing
        ]
        for table in sorted(name for name in existing if name.startswith("life_")):
            tables.append(
                {
                    "name": table,
                    "rows": _count_rows(conn, table),
                    "category": "legacy_life_residual",
                }
            )
        return tables
    finally:
        conn.close()


def _count_rows(conn: sqlite3.Connection, table: str) -> int:
    quoted = '"' + table.replace('"', '""') + '"'
    return int(conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])


def _delete_table_rows(db_path: Path, table_reports: list[dict[str, Any]]) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute("BEGIN")
            for item in table_reports:
                table = str(item["name"])
                quoted = '"' + table.replace('"', '""') + '"'
                conn.execute(f"DELETE FROM {quoted}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _schema_checks(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"queryable": False, "captures_columns": []}
    conn = sqlite3.connect(str(db_path))
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(captures)")]
        conn.execute("SELECT COUNT(*) FROM captures").fetchone()
        return {
            "queryable": "id" in columns and "file_path" in columns,
            "captures_columns": columns,
        }
    except sqlite3.Error as exc:
        return {
            "queryable": False,
            "captures_columns": [],
            "error": str(exc),
        }
    finally:
        conn.close()


def _file_targets(legacy_store: Path, repo_root: Path) -> list[dict[str, Any]]:
    return [
        _path_report(legacy_store / "_archive" / "write_backups"),
        _path_report(legacy_store / "_archive" / "garden_migrations"),
        _path_report(repo_root / "backups"),
        _path_report(repo_root / "data" / "backups"),
        _path_report(repo_root / "data" / "exports" / "theme"),
        _path_report(repo_root / "data" / "repair_reports"),
    ]


def _allowed_delete_targets(legacy_store: Path, repo_root: Path) -> list[Path]:
    return [
        legacy_store,
        repo_root / "backups",
        repo_root / "data" / "backups",
        repo_root / "data" / "exports" / "theme",
        repo_root / "data" / "repair_reports",
    ]


def _automation_preflight(
    *,
    legacy_store: Path,
    new_storage_root: Path,
    repo_root: Path,
) -> dict[str, Any]:
    watcher_processes = _matching_processes(["watch_vault.py"])
    repair_processes = _matching_processes(["repair_data.py"])
    theme_processes = _matching_processes(["theme_scan", "theme_scan_cron.py"])
    gateway_processes = _matching_processes(["hermes_cli.main gateway run"])
    cron_jobs = _cron_jobs_report(DEFAULT_CRON_JOBS_PATH.expanduser())
    enabled_cron_jobs = [
        job["name"]
        for job in cron_jobs.get("jobs", [])
        if job.get("enabled")
    ]
    watcher_plist = _watcher_plist_report(legacy_store=legacy_store)

    blockers: list[str] = []
    if watcher_processes:
        blockers.append("watch_vault.py process is running")
    if repair_processes:
        blockers.append("repair_data.py process is running")
    if theme_processes:
        blockers.append("theme_scan process is running")
    if enabled_cron_jobs:
        blockers.append("enabled cron jobs: " + ", ".join(enabled_cron_jobs))
    if watcher_plist.get("uses_legacy_store"):
        blockers.append(f"{WATCHER_LABEL} plist still points at legacy store")

    return {
        "blockers": blockers,
        "watcher_processes": watcher_processes,
        "repair_processes": repair_processes,
        "theme_processes": theme_processes,
        "gateway_processes": gateway_processes,
        "cron_jobs": cron_jobs,
        "watcher_launchagent": watcher_plist,
        "expected_new_storage_root": str(new_storage_root),
        "repo_root": str(repo_root),
    }


def _matching_processes(patterns: list[str]) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["ps", "ax", "-o", "pid=", "-o", "command="],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return []

    matches: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        if not command or not pid_text.isdigit():
            continue
        if _is_ignored_process_command(command):
            continue
        if any(pattern in command for pattern in patterns):
            matches.append({"pid": int(pid_text), "command": command})
    return matches


def _is_ignored_process_command(command: str) -> bool:
    parts = (Path(part).name for part in command.split())
    if any(part in {"pytest", "py.test", "rg", "grep"} for part in parts):
        return True
    return "ps ax" in command and "-o" in command and "command" in command


def _cron_jobs_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "jobs": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "path": str(path),
            "exists": True,
            "error": str(exc),
            "jobs": [],
        }

    raw_jobs = payload.get("jobs") if isinstance(payload, dict) else payload
    jobs: list[dict[str, Any]] = []
    if isinstance(raw_jobs, list):
        for item in raw_jobs:
            if not isinstance(item, dict):
                continue
            jobs.append(
                {
                    "name": str(item.get("name") or item.get("id") or ""),
                    "enabled": bool(item.get("enabled", True)),
                    "script": str(item.get("script") or ""),
                    "paused_reason": str(item.get("paused_reason") or ""),
                }
            )
    return {"path": str(path), "exists": True, "jobs": jobs}


def _watcher_plist_report(*, legacy_store: Path) -> dict[str, Any]:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{WATCHER_LABEL}.plist"
    report: dict[str, Any] = {
        "label": WATCHER_LABEL,
        "path": str(plist_path),
        "exists": plist_path.exists(),
        "configured_vault_path": "",
        "uses_legacy_store": False,
    }
    if not plist_path.exists():
        return report
    try:
        with plist_path.open("rb") as handle:
            payload = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        report["error"] = str(exc)
        return report

    args = payload.get("ProgramArguments")
    if isinstance(args, list):
        for index, value in enumerate(args):
            if value == "--vault" and index + 1 < len(args):
                configured = Path(str(args[index + 1])).expanduser()
                report["configured_vault_path"] = str(configured)
                report["uses_legacy_store"] = configured == legacy_store
                break
    return report


def _storage_files(storage: Path) -> list[str]:
    if not storage.exists():
        return []
    return sorted(
        path.relative_to(storage).as_posix()
        for path in storage.rglob("*")
        if path.is_file()
    )


def _clear_storage_root(storage: Path) -> None:
    if not storage.exists():
        return
    if storage.is_file() or storage.is_symlink():
        storage.unlink()
        return
    for child in storage.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _path_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "files": 0, "markdown_files": 0}
    if path.is_file():
        return {
            "path": str(path),
            "exists": True,
            "files": 1,
            "markdown_files": 1 if path.suffix == ".md" else 0,
        }
    files = [item for item in path.rglob("*") if item.is_file()]
    return {
        "path": str(path),
        "exists": True,
        "files": len(files),
        "markdown_files": sum(1 for item in files if item.suffix == ".md"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset local Hermes Knowledge personal data after dry-run review."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print reset plan only.")
    mode.add_argument("--apply", action="store_true", help="Apply reset after confirmation.")
    mode.add_argument("--verify-empty", action="store_true", help="Verify post-reset empty state.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--legacy-store", default=str(DEFAULT_LEGACY_STORE))
    parser.add_argument("--new-storage-root", default=str(DEFAULT_NEW_STORAGE_ROOT))
    parser.add_argument("--repo-root", default=str(DEFAULT_REPO_ROOT))
    parser.add_argument("--life-db", default=str(DEFAULT_LIFE_DB_PATH))
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)

    if args.verify_empty:
        result = verify_empty_reset(
            db_path=args.db,
            legacy_store=args.legacy_store,
            new_storage_root=args.new_storage_root,
            repo_root=args.repo_root,
            life_db_path=args.life_db,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    confirmation = args.confirm
    if args.apply and not confirmation:
        confirmation = input(f"Type {CONFIRMATION_PHRASE!r} to apply reset: ")

    result = reset_local_data(
        db_path=args.db,
        legacy_store=args.legacy_store,
        new_storage_root=args.new_storage_root,
        repo_root=args.repo_root,
        apply=args.apply,
        confirmation=confirmation,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
