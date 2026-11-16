"""Small path helpers shared by Knowledge File writes."""
from __future__ import annotations

from pathlib import Path


def safe_vault_relative_path(value: str | None, *, default: str = "Inbox") -> str:
    """Return a vault-relative logical folder, rejecting traversal/absolute paths."""
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return default
    candidate = Path(text)
    if candidate.is_absolute() or ".." in candidate.parts:
        return default
    parts = [part for part in candidate.parts if part not in {"", "."}]
    return "/".join(parts) or default
