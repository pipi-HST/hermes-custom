"""Small helper for recording bridge entrypoint runs."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from typing import Any

from agents.run_evidence import extract_run_evidence
from db.agent_run_repo import AgentRunRepo


def run_with_ledger(
    conn: sqlite3.Connection,
    *,
    agent: str,
    intent: str,
    trigger: str = "wechat",
    input_ref: dict[str, Any] | None = None,
    operation: Callable[[], Mapping[str, Any]],
) -> Mapping[str, Any]:
    repo = AgentRunRepo(conn)
    run = repo.start_run(
        agent=agent,
        intent=intent,
        trigger=trigger,
        input_ref=input_ref or {},
    )
    repo.record_event(run["id"], event_type="started", payload=input_ref or {})
    try:
        result = operation()
    except Exception as exc:
        repo.fail_run(
            run["id"],
            errors=[str(exc)],
            evidence={"has_envelope": False},
        )
        repo.record_event(run["id"], event_type="failed", payload={"error": str(exc)})
        raise

    extracted = extract_run_evidence(agent, intent, result)
    if extracted["status"] == "error":
        repo.fail_run(
            run["id"],
            errors=extracted["errors"] or ["agent returned error"],
            evidence=extracted["evidence"],
        )
        repo.record_event(
            run["id"],
            event_type="failed",
            payload={"errors": extracted["errors"] or ["agent returned error"]},
        )
    else:
        repo.complete_run(
            run["id"],
            output_ref=extracted["output_ref"],
            evidence=extracted["evidence"],
            warnings=extracted["warnings"],
            status=extracted["status"],
        )
        repo.record_event(
            run["id"],
            event_type="completed",
            payload={
                "status": extracted["status"],
                "evidence": extracted["evidence"],
            },
        )
    return result
