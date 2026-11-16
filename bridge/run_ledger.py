"""Small helper for recording bridge entrypoint runs."""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Mapping
from typing import Any

from agents.run_evidence import extract_input_object_refs, extract_run_evidence
from bridge.ai_cancel import AIOperationCancelled
from db.agent_run_repo import AgentRunRepo

# Only one logical run may be in flight at a time, so two bridge entrypoints
# cannot interleave a start/complete pair.  This deliberately does *not* reuse
# the connection lock: db.concurrency already serialises every statement, and
# holding the connection lock for a whole operation would queue all API reads
# behind slow LLM pipelines (summarize 300s / outliner 120s).
_RUN_LOCK = threading.RLock()


def run_with_ledger(
    conn: sqlite3.Connection,
    *,
    agent: str,
    intent: str,
    trigger: str = "wechat",
    input_ref: dict[str, Any] | None = None,
    operation: Callable[[], Mapping[str, Any]],
    run_id_out: dict[str, str] | None = None,
) -> Mapping[str, Any]:
    with _RUN_LOCK:
        return _run_with_ledger_unlocked(
            conn,
            agent=agent,
            intent=intent,
            trigger=trigger,
            input_ref=input_ref,
            operation=operation,
            run_id_out=run_id_out,
        )


def _run_with_ledger_unlocked(
    conn: sqlite3.Connection,
    *,
    agent: str,
    intent: str,
    trigger: str,
    input_ref: dict[str, Any] | None,
    operation: Callable[[], Mapping[str, Any]],
    run_id_out: dict[str, str] | None = None,
) -> Mapping[str, Any]:
    repo = AgentRunRepo(conn)
    run = repo.start_run(
        agent=agent,
        intent=intent,
        trigger=trigger,
        input_ref=input_ref or {},
    )
    if run_id_out is not None:
        run_id_out["run_id"] = str(run["id"])
    repo.record_event(run["id"], event_type="started", payload=input_ref or {})
    repo.record_object_refs(
        run["id"],
        extract_input_object_refs(intent, input_ref or {}),
    )
    try:
        result = operation()
    except AIOperationCancelled as exc:
        repo.complete_run(
            run["id"],
            output_ref={},
            evidence={
                "has_envelope": False,
                "cancelled_stage": exc.stage,
                "operation_id": exc.operation_id,
            },
            warnings=[],
            status="cancelled",
        )
        repo.record_event(
            run["id"],
            event_type="cancelled",
            payload={"stage": exc.stage, "operation_id": exc.operation_id},
        )
        raise
    except Exception as exc:
        repo.fail_run(
            run["id"],
            errors=[str(exc)],
            evidence={"has_envelope": False},
        )
        repo.record_event(run["id"], event_type="failed", payload={"error": str(exc)})
        raise

    extracted = extract_run_evidence(agent, intent, result)
    repo.record_object_refs(run["id"], extracted.get("object_refs") or [])
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
