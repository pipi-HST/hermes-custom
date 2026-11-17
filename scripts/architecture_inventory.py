"""Generate the architecture inventory and fail when it drifts.

The point is a single, machine-generated view of:
- Service HTTP routes and Runtime knowledge routes,
- which Runtime routes still touch a local SQLite/module,
- tables and the modules that write them,
- docs status coverage.

Run ``python scripts/architecture_inventory.py`` to write
``docs/INVENTORY.md``; ``--check`` fails when the file is stale;
``--strict`` also fails when a Runtime route loses its HTTP delegation
without being on the explicit local-only allowlist.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / "docs" / "INVENTORY.md"
RUNTIME_ROOT = Path(
    os.environ.get("HERMES_RUNTIME_ROOT", "").strip()
    or (PROJECT_ROOT.parent / "hermes-agent-main")
).expanduser()

# Routes that are intentionally local-only (they open local files/processes).
LOCAL_ONLY_ALLOWED = {
    "/api/knowledge/runtime-check",
    "/api/knowledge/library/items/{kind}/{item_id}/open-local",
}

ROUTE_DECORATOR = re.compile(
    r'@(?:app|router)\.(get|post|patch|delete|put)\("([^"]+)"'
)
TABLE_RE = re.compile(
    r"CREATE TABLE IF NOT EXISTS (\w+)|CREATE TABLE (\w+)"
)


def _service_routes() -> list[tuple[str, str]]:
    routes: list[tuple[str, str]] = []
    for path in sorted((PROJECT_ROOT / "api" / "routes").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for method, route in ROUTE_DECORATOR.findall(text):
            routes.append((method.upper(), f"/api{route}"))
    return routes


def _runtime_routes() -> list[dict]:
    path = RUNTIME_ROOT / "hermes_cli" / "web_server.py"
    if not path.is_file():
        return []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    routes: list[dict] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        route = ""
        method = ""
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr in {"get", "post", "patch", "delete", "put"}
                and dec.args
                and isinstance(dec.args[0], ast.Constant)
                and isinstance(dec.args[0].value, str)
            ):
                route = dec.args[0].value
                method = dec.func.attr.upper()
        if not route.startswith("/api/knowledge"):
            continue
        has_client = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "get_client"
            for n in ast.walk(node)
        )
        local = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id in {"_knowledge_conn", "_knowledge_custom_module", "_knowledge_db_path"}
            for n in ast.walk(node)
        )
        routes.append(
            {
                "method": method,
                "route": route,
                "delegates": has_client,
                "local": local,
            }
        )
    return routes


def _tables() -> list[str]:
    """Return the business tables from the schema, not from a local database.

    Reading ``data/captures.db`` made the generated file depend on whether a
    developer had ever run the app; CI has no database and produced a different
    inventory.  ``captures_new`` is the interrupted-swap scratch table and is
    not part of the schema; ``capture_search_fts`` is created conditionally.
    """
    schema = (PROJECT_ROOT / "db" / "schema.py").read_text(encoding="utf-8")
    names = set()
    for match in TABLE_RE.finditer(schema):
        name = match.group(1) or match.group(2)
        if name:
            names.add(name)
    names.discard("captures_new")
    names.add("capture_search_fts")
    return sorted(names)


def _writers(tables: list[str]) -> dict[str, list[str]]:
    patterns = {
        table: re.compile(
            rf"(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM)\s+{re.escape(table)}\b",
            re.IGNORECASE,
        )
        for table in tables
    }
    writers: dict[str, set[str]] = {table: set() for table in tables}
    roots = [PROJECT_ROOT / folder for folder in ("agents", "bridge", "api", "db", "workers")]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            for table, pattern in patterns.items():
                if pattern.search(text):
                    writers[table].add(relative)
    return {table: sorted(paths) for table, paths in writers.items()}


def _docs_status() -> tuple[int, int, list[str]]:
    current = 0
    total = 0
    missing: list[str] = []
    candidates = [p for p in PROJECT_ROOT.glob("*.md")] + list(
        (PROJECT_ROOT / "docs").glob("*.md")
    )
    for path in candidates:
        if path.name in {"SOLUTION_SUMMARY.md", "INVENTORY.md"}:
            continue
        total += 1
        text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        if not text.startswith("---\n"):
            missing.append(path.relative_to(PROJECT_ROOT).as_posix())
            continue
        end = text.find("\n---", 4)
        block = text[4:end] if end >= 0 else ""
        status = ""
        for line in block.splitlines():
            if line.startswith("status:"):
                status = line.split(":", 1)[1].strip()
        if status == "current":
            current += 1
    return current, total, missing


def build() -> str:
    service = _service_routes()
    runtime = _runtime_routes()
    tables = _tables()
    writers = _writers(tables)
    current, total, missing = _docs_status()

    lines = [
        "---",
        "status: current",
        "owner: knowledge-service",
        "last_verified: 2026-11-17",
        "verified_against: generated by scripts/architecture_inventory.py",
        "---",
        "",
        "# Architecture inventory",
        "",
        "> Generated by `scripts/architecture_inventory.py`. Do not hand-edit.",
        "",
        f"- Service routes: {len(service)}",
        f"- Runtime knowledge routes: {len(runtime)}",
        f"- Runtime knowledge routes without HTTP delegation: "
        f"{sum(1 for r in runtime if r['local'] and not r['delegates'])}",
        f"- Tables: {len(tables)}",
        f"- Current docs: {current} / {total}",
        "",
        "## Runtime knowledge routes",
        "",
        "| Method | Route | HTTP delegation | Local SQL/module |",
        "|---|---|---|---|",
    ]
    for item in sorted(runtime, key=lambda r: r["route"]):
        lines.append(
            f"| {item['method']} | `{item['route']}` | "
            f"{'yes' if item['delegates'] else 'no'} | "
            f"{'yes' if item['local'] else 'no'} |"
        )
    lines += ["", "## Service routes", "", "| Method | Route |", "|---|---|"]
    for method, route in sorted(service, key=lambda item: item[1]):
        lines.append(f"| {method} | `{route}` |")

    lines += ["", "## Table writers", "", "| Table | Writers |", "|---|---|"]
    for table in tables:
        paths = writers.get(table) or []
        cell = "<br>".join(f"`{path}`" for path in paths[:6]) or "-"
        if len(paths) > 6:
            cell += f"<br>… +{len(paths) - 6}"
        lines.append(f"| `{table}` | {cell} |")

    if missing:
        lines += ["", "## Docs without status", ""]
        lines += [f"- `{path}`" for path in missing]

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    runtime_present = (RUNTIME_ROOT / "hermes_cli" / "web_server.py").is_file()
    if not runtime_present:
        print(
            "runtime checkout not found; architecture inventory check skipped "
            "(set HERMES_RUNTIME_ROOT or check out hermes-agent-main)"
        )
        return 0

    content = build()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != content:
            print("docs/INVENTORY.md is stale; run scripts/architecture_inventory.py")
            return 1
    else:
        OUTPUT.write_text(content, encoding="utf-8")
        print(OUTPUT)

    if args.strict:
        offending = [
            item
            for item in _runtime_routes()
            if item["local"] and not item["delegates"]
            and item["route"] not in LOCAL_ONLY_ALLOWED
        ]
        if offending:
            for item in offending:
                print(f"{item['route']}: local access without get_client()")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
