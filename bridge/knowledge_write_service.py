"""Shared helpers for durable Knowledge artifact writes."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


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
        backup_path = ""
        if request.requires_backup and target.exists():
            backup_path = self._backup_existing_file(target, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(request.content, encoding="utf-8")
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
            / f"{relative_path.stem}-{timestamp}{relative_path.suffix}"
        )
        backup = self.vault_path / backup_relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        return backup_relative.as_posix()


def _safe_relative_path(value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"artifact path must stay inside the vault: {value}")
    if not relative.as_posix().strip():
        raise ValueError("artifact path cannot be empty")
    return relative
