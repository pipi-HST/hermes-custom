"""Small helpers for writing user-requested Hermes file exports."""
from __future__ import annotations

import re
from pathlib import Path


def safe_export_stem(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "-", value or "").strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or "hermes-export"


def write_markdown_export(*, base_dir: Path, stem: str, content: str) -> str:
    base_dir = base_dir.expanduser()
    base_dir.mkdir(parents=True, exist_ok=True)
    path = (base_dir / f"{safe_export_stem(stem)}.md").resolve()
    text = (content or "").rstrip() + "\n"
    path.write_text(text, encoding="utf-8")
    return str(path)
