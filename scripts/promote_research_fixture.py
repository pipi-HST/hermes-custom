#!/usr/bin/env python3
"""Promote a captured research failure fixture draft into the golden JSONL set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "tests" / "fixtures" / "research_eval_cases.jsonl"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no}: expected object")
            cases.append(value)
    return cases


def validate_fixture(fixture: dict[str, Any], existing_cases: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    case_id = str(fixture.get("id") or "").strip()
    if not case_id:
        errors.append("fixture.id is required")
    existing_ids = {str(case.get("id") or "") for case in existing_cases}
    if case_id in existing_ids:
        errors.append(f"fixture.id already exists: {case_id}")
    for key in ("category", "mode", "query", "planner", "expect"):
        if key not in fixture:
            errors.append(f"fixture.{key} is required")
    if not (fixture.get("search_results") or fixture.get("local_sources")):
        errors.append("fixture needs search_results or local_sources")
    if fixture.get("critic_decisions") is None:
        errors.append("fixture.critic_decisions is required, even if []")
    return errors


def normalized_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    ordered_keys = [
        "id",
        "category",
        "mode",
        "query",
        "selection_context",
        "article_context",
        "planner",
        "local_sources",
        "search_results",
        "extract_results",
        "critic_decisions",
        "synth_answer",
        "expect",
        "expect_live",
    ]
    result = {key: fixture[key] for key in ordered_keys if key in fixture}
    for key, value in fixture.items():
        if key not in result:
            result[key] = value
    return result


def promote_fixture(fixture_path: Path, cases_path: Path, *, dry_run: bool) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(fixture, dict):
        raise ValueError("fixture draft must be a JSON object")
    existing_cases = load_jsonl(cases_path)
    errors = validate_fixture(fixture, existing_cases)
    if errors:
        raise ValueError("; ".join(errors))
    normalized = normalized_fixture(fixture)
    if not dry_run:
        cases_path.parent.mkdir(parents=True, exist_ok=True)
        with cases_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(normalized, ensure_ascii=False, sort_keys=False) + "\n")
    return {
        "id": normalized["id"],
        "cases_path": str(cases_path),
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path, help="Path to <case>.fixture.json")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="Target research_eval_cases.jsonl")
    parser.add_argument("--dry-run", action="store_true", help="Validate without appending")
    args = parser.parse_args(argv)

    try:
        result = promote_fixture(args.fixture, args.cases, dry_run=args.dry_run)
    except Exception as exc:
        print(f"promote research fixture failed: {exc}", file=sys.stderr)
        return 1
    action = "validated" if args.dry_run else "promoted"
    print(f"{action} research fixture {result['id']} -> {result['cases_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
