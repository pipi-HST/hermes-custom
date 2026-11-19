"""Shared helpers for durable Knowledge artifact writes."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4


# Acquire before opening a SQLite write transaction when an operation needs both.
markdown_write_lock = RLock()


class MarkdownVersionConflict(ValueError):
    """The note no longer matches the version a write was prepared from."""


@dataclass(frozen=True)
class MarkdownWriteRequest:
    kind: str
    relative_path: str
    content: str
    db_object_id: str = ""
    db_table: str = ""
    requires_backup: bool = False


@dataclass(frozen=True)
class KnowledgeWriteResult:
    kind: str
    file_path: str
    db_object_id: str = ""
    db_table: str = ""
    backup_path: str = ""
    write_job_id: str = ""
    warnings: list[str] = field(default_factory=list)


class KnowledgeWriteService:
    def __init__(self, vault_path: str | Path):
        self.vault_path = Path(vault_path).expanduser()

    def write_markdown_artifact(
        self,
        request: MarkdownWriteRequest,
        *,
        expected_previous: str | None = None,
        register_backup: Callable[[str], None] | None = None,
    ) -> KnowledgeWriteResult:
        relative_path = _safe_relative_path(request.relative_path)
        target = self.vault_path / relative_path
        backup_path = ""

        def check_version() -> None:
            _ensure_path_inside_vault(self.vault_path, target, request.relative_path)
            if expected_previous is not None:
                try:
                    current = target.read_bytes()
                except FileNotFoundError:
                    current = None
                if current != expected_previous.encode("utf-8"):
                    raise MarkdownVersionConflict(request.relative_path)

        # Keep version checks and replacement together for writers using this
        # service. Check again just before replacing in case of an outside edit.
        with markdown_write_lock:
            check_version()
            if request.requires_backup and (expected_previous is not None or target.exists()):
                backup_path = self._backup_existing_file(target, relative_path, snapshot=expected_previous)
                if register_backup is not None:
                    register_backup(backup_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            _ensure_path_inside_vault(self.vault_path, target, request.relative_path)
            _atomic_write_text(
                target, request.content,
                before_replace=check_version if expected_previous is not None else None,
            )
        return KnowledgeWriteResult(
            kind=request.kind,
            file_path=relative_path.as_posix(),
            db_object_id=request.db_object_id,
            db_table=request.db_table,
            backup_path=backup_path,
        )

    def _backup_existing_file(
        self, target: Path, relative_path: Path, *, snapshot: str | None = None,
    ) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        # A backup is nested deeper than the original. Keep a readable prefix
        # without repeating the entire title alongside the timestamp and UUID.
        stem = relative_path.stem[:12]
        backup_relative = (
            Path("_archive")
            / "write_backups"
            / relative_path.parent
            / f"{stem}-{timestamp}-{uuid4().hex}{relative_path.suffix}"
        )
        backup = self.vault_path / backup_relative
        _ensure_path_inside_vault(self.vault_path, backup, backup_relative.as_posix())
        backup.parent.mkdir(parents=True, exist_ok=True)
        _ensure_path_inside_vault(self.vault_path, backup, backup_relative.as_posix())
        if snapshot is None:
            shutil.copy2(target, backup)
        else:
            # Use the verified bytes, not a second read racing with an editor.
            _atomic_write_text(backup, snapshot)
        return backup_relative.as_posix()


def _safe_relative_path(value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"artifact path must stay inside Knowledge File storage: {value}")
    if not relative.as_posix().strip():
        raise ValueError("artifact path cannot be empty")
    return relative


def _ensure_path_inside_vault(vault_path: Path, target: Path, display_path: str) -> None:
    try:
        target.resolve().relative_to(vault_path.resolve())
    except ValueError as exc:
        raise ValueError(
            f"artifact path must stay inside Knowledge File storage: {display_path}"
        ) from exc


def _atomic_write_text(
    target: Path, content: str, *, before_replace: Callable[[], None] | None = None,
) -> None:
    from bridge.atomic_io import atomic_write_text

    atomic_write_text(target, content, before_replace=before_replace)
