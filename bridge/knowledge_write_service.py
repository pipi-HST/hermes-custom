"""Shared helpers for durable Knowledge artifact writes."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


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

    def write_markdown_artifact(self, request: MarkdownWriteRequest) -> KnowledgeWriteResult:
        relative_path = _safe_relative_path(request.relative_path)
        target = self.vault_path / relative_path
        _ensure_path_inside_vault(self.vault_path, target, request.relative_path)
        backup_path = ""
        if request.requires_backup and target.exists():
            backup_path = self._backup_existing_file(target, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        _ensure_path_inside_vault(self.vault_path, target, request.relative_path)
        _atomic_write_text(target, request.content)
        return KnowledgeWriteResult(
            kind=request.kind,
            file_path=relative_path.as_posix(),
            db_object_id=request.db_object_id,
            db_table=request.db_table,
            backup_path=backup_path,
        )

    def _backup_existing_file(self, target: Path, relative_path: Path) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_relative = (
            Path("_archive")
            / "write_backups"
            / relative_path.parent
            / f"{relative_path.stem}-{timestamp}-{uuid4().hex}{relative_path.suffix}"
        )
        backup = self.vault_path / backup_relative
        _ensure_path_inside_vault(self.vault_path, backup, backup_relative.as_posix())
        backup.parent.mkdir(parents=True, exist_ok=True)
        _ensure_path_inside_vault(self.vault_path, backup, backup_relative.as_posix())
        shutil.copy2(target, backup)
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


def _atomic_write_text(target: Path, content: str) -> None:
    from bridge.atomic_io import atomic_write_text

    atomic_write_text(target, content)
