"""Fail when current docs are stale, broken, or use local absolute paths."""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CURRENT_DOCS = [
    "README.md",
    "ARCHITECTURE.md",
    "GLOSSARY.md",
    "RUNBOOK.md",
    "AGENTS.md",
    "CONTEXT.md",
    "SETUP.md",
    "HANDOFF.md",
    "docs/README.md",
    "docs/CURRENT-STATE.md",
    "docs/PRODUCT-REFACTOR-STORY.md",
    "docs/PLAN-P10-T9-T26.md",
    "docs/local-data-reset-runbook.md",
]
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _check_current_file(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    if "/Users/" in text:
        errors.append(f"{path}: current doc contains a local absolute path")
    if path.name == "HANDOFF.md" and len(text.splitlines()) > 3:
        errors.append(f"{path}: root HANDOFF must stay a one-line pointer")
    for target in LINK_RE.findall(text):
        target = target.strip()
        if not target or target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#", 1)[0].strip()
        if not target:
            continue
        if not (path.parent / target).exists():
            errors.append(f"{path}: broken link {target}")
    return errors


def _check_history_status() -> list[str]:
    errors: list[str] = []
    history = PROJECT_ROOT / "docs" / "history"
    for sub in ("plans", "specs", "reports"):
        for path in sorted((history / sub).glob("*.md")):
            text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
            if not text.startswith("---\nstatus: historical"):
                errors.append(f"{path}: missing historical status header")
    return errors


def main() -> int:
    errors: list[str] = []
    for relative in CURRENT_DOCS:
        path = PROJECT_ROOT / relative
        if not path.exists():
            errors.append(f"{relative}: missing")
            continue
        errors.extend(_check_current_file(path))
    errors.extend(_check_history_status())
    if errors:
        print("\n".join(errors))
        return 1
    print(f"docs ok ({len(CURRENT_DOCS)} current docs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
