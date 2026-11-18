"""Filesystem helpers for durable Knowledge artifacts.

All durable Markdown writes go through :func:`atomic_write_text`: write to a
temporary file in the same directory, fsync, then ``os.replace``.  A crash
mid-write can therefore never truncate an existing note.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def resolve_inside(root: Path, relative_path: str | Path) -> Path:
    """Resolve ``relative_path`` under ``root``, rejecting escapes."""
    root_resolved = root.expanduser().resolve()
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"path must stay inside {root}: {relative_path}")
    target = (root_resolved / candidate).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"path must stay inside {root}: {relative_path}") from exc
    return target


def atomic_write_text(
    target: Path, content: str, *, before_replace: Callable[[], None] | None = None,
) -> None:
    """Atomically replace ``target`` with ``content`` (UTF-8)."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        # Keep the sibling name short for nested vaults on Windows.
        prefix=".hw-",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if before_replace is not None:
            before_replace()
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)


def backup_file(target: Path, backup_root: Path, *, label: str = "") -> str:
    """Copy ``target`` into ``backup_root`` and return the relative backup path."""
    target = Path(target)
    backup_root = Path(backup_root)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{target.stem}-{timestamp}-{uuid4().hex}{target.suffix}"
    if label:
        name = f"{target.stem}-{label}-{timestamp}-{uuid4().hex}{target.suffix}"
    backup = backup_root / name
    backup.parent.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copy2(target, backup)
    return str(backup)
