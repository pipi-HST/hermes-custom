"""SQLite repository for Hermes Agent run ledger."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


class AgentRunRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def start_run(
        self,
        *,
        agent: str,
        intent: str,
        trigger: str = "",
        input_ref: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run_id = uuid.uuid4().hex
        now = _now()
        self.conn.execute(
            """INSERT INTO agent_runs
               (id, agent, intent, trigger, status, input_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'running', ?, ?, ?)""",
            (
                run_id,
                agent,
                intent,
                trigger,
                _json(input_ref or {}),
                now,
                now,
            ),
        )
        self.conn.commit()
        return {
            "id": run_id,
            "agent": agent,
            "intent": intent,
            "trigger": trigger,
            "status": "running",
            "input_json": _json(input_ref or {}),
        }

    def record_event(
        self,
        run_id: str,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO agent_run_events (run_id, event_type, payload_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (run_id, event_type, _json(payload or {}), _now()),
        )
        self.conn.commit()

    def complete_run(
        self,
        run_id: str,
        *,
        output_ref: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        status: str = "success",
    ) -> None:
        self._finish_run(
            run_id,
            status=status,
            output_ref=output_ref or {},
            evidence=evidence or {},
            warnings=warnings or [],
            errors=[],
        )

    def fail_run(
        self,
        run_id: str,
        *,
        errors: list[str],
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self._finish_run(
            run_id,
            status="error",
            output_ref={},
            evidence=evidence or {},
            warnings=[],
            errors=errors,
        )

    def latest(self, *, limit: int = 5) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT * FROM agent_runs
               ORDER BY datetime(created_at) DESC, id DESC
               LIMIT ?""",
            (max(1, int(limit)),),
        ).fetchall()

    def events(self, run_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT * FROM agent_run_events
               WHERE run_id = ?
               ORDER BY id ASC""",
            (run_id,),
        ).fetchall()

    def _finish_run(
        self,
        run_id: str,
        *,
        status: str,
        output_ref: dict[str, Any],
        evidence: dict[str, Any],
        warnings: list[str],
        errors: list[str],
    ) -> None:
        now = _now()
        self.conn.execute(
            """UPDATE agent_runs
               SET status = ?,
                   output_json = ?,
                   evidence_json = ?,
                   warnings_json = ?,
                   errors_json = ?,
                   updated_at = ?,
                   completed_at = ?
               WHERE id = ?""",
            (
                status,
                _json(output_ref),
                _json(evidence),
                _json(warnings),
                _json(errors),
                now,
                now,
                run_id,
            ),
        )
        self.conn.commit()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
