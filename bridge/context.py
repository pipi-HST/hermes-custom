"""Shared runtime context for Knowledge bridge calls."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from db.repo import CaptureRepo
from db.reading_repo import ReadingRepo


@dataclass(frozen=True)
class KnowledgeContext:
    config: Any
    conn: sqlite3.Connection
    vault_path: Path

    def capture_repo(self) -> CaptureRepo:
        return CaptureRepo(self.conn)

    def reading_repo(self) -> ReadingRepo:
        return ReadingRepo(self.conn)

    def capture_agent(self):
        from agents.capture_agent import CaptureAgent

        return CaptureAgent(self.config, conn=self.conn)
