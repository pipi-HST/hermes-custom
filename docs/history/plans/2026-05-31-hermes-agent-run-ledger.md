---
status: historical
execution_status: done
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Hermes Agent Run Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a small Agent Run Ledger so Hermes records what each knowledge Agent actually did, what durable artifacts it produced, and whether completion evidence is sufficient.

**Architecture:** Keep the existing Agent behavior intact. Add a SQLite-backed run ledger in `hermes-custom`, wrap selected bridge/tool entrypoints to record start/events/completion, and expose a read-only Hermes runtime tool for asking "what just ran?". Completion evidence is derived from existing Agent Result Envelopes and local reports, not from natural-language claims.

**Tech Stack:** Python, SQLite, existing Hermes Agent Result Envelope, pytest, existing `hermes-custom` bridge modules, existing `hermes-agent-main` tool registry.

---

## Context

This plan borrows one useful idea from Memex: AI work should have a durable lifecycle and observable evidence. Hermes should not trust "I finished" as proof. For each important knowledge workflow, Hermes should record:

- Trigger: what caused the run.
- Input reference: what the Agent was asked to process.
- Events: important steps, retries, tool calls, or warnings.
- Output reference: durable artifacts such as draft IDs, card IDs, report paths, source counts, or written files.
- Completion evidence: machine-checkable proof that the run did the expected work.

This is not a new UI, not a scheduler rewrite, and not an Agent behavior redesign.

## Scope

P0 covers these workflows:

- `audit_library`
- `theme_scan`
- `synthesize_garden`
- `revise_garden_draft`
- `commit_garden_draft`
- `search_my_notes`
- `daily_review`
- `review_followup`

P0 does not cover:

- Capture draft generation internals.
- Life Assistant.
- A visual dashboard.
- Automatic background scheduling changes.
- Cross-Agent orchestration beyond recording evidence.

## File Structure

Create:

- `db/agent_run_repo.py`: SQLite repository for run ledger writes and reads.
- `bridge/agent_run_bridge.py`: read-only bridge used by Hermes runtime to show recent runs.
- `bridge/run_ledger.py`: small helper for instrumenting bridge functions.
- `agents/run_evidence.py`: evidence extraction from Agent Result Envelopes and library reports.
- `tests/test_agent_run_repo.py`: repository lifecycle tests.
- `tests/test_run_evidence.py`: evidence extraction tests.
- `tests/test_run_ledger.py`: bridge instrumentation tests.
- `/Users/pipi/Downloads/hermes-agent-main/tools/agent_run_status_tool.py`: runtime tool to query recent Hermes Agent runs.
- `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_agent_run_status_tool.py`: runtime tool test.

Modify:

- `db/schema.py`: create `agent_runs` and `agent_run_events`.
- `bridge/librarian_bridge.py`: record `audit_library` runs.
- `bridge/theme_bridge.py`: record `theme_scan` runs.
- `bridge/garden_bridge.py`: record Garden draft and commit runs.
- `bridge/rag_bridge.py`: record search runs.
- `bridge/review_bridge.py`: record review runs.
- `/Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py`: include the new read-only tool in the personal assistant toolset.

## Data Model

Use two tables. Keep JSON fields as text so P0 stays simple and compatible with the existing SQLite style.

`agent_runs`:

- `id TEXT PRIMARY KEY`
- `agent TEXT NOT NULL`
- `intent TEXT NOT NULL`
- `trigger TEXT NOT NULL DEFAULT ''`
- `status TEXT NOT NULL DEFAULT 'running'`
- `input_json TEXT NOT NULL DEFAULT '{}'`
- `output_json TEXT NOT NULL DEFAULT '{}'`
- `evidence_json TEXT NOT NULL DEFAULT '{}'`
- `warnings_json TEXT NOT NULL DEFAULT '[]'`
- `errors_json TEXT NOT NULL DEFAULT '[]'`
- `created_at TEXT NOT NULL DEFAULT (datetime('now'))`
- `updated_at TEXT NOT NULL DEFAULT (datetime('now'))`
- `completed_at TEXT`

`agent_run_events`:

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `run_id TEXT NOT NULL`
- `event_type TEXT NOT NULL`
- `payload_json TEXT NOT NULL DEFAULT '{}'`
- `created_at TEXT NOT NULL DEFAULT (datetime('now'))`

Valid `agent_runs.status` values:

- `running`
- `success`
- `error`
- `incomplete`

`incomplete` means the function returned without a hard exception, but completion evidence says the expected durable result is missing.

## Completion Evidence Rules

Generic envelope evidence:

- `has_envelope`: `true` when the result has the standard envelope shape.
- `status`: copied from the envelope.
- `artifact_count`: number of `durable_artifacts`.
- `source_count`: number of `source_map` entries.
- `warning_count`: number of warnings.
- `error_count`: number of errors.

Workflow-specific evidence:

- `audit_library`: `database_sync_checked`, `outlines_checked`, `property_gaps_checked`, `embedding_index_checked`, `report_path` when present.
- `theme_scan`: `scanned_count`, `theme_pages_written`, `pending_questions_created`, `report_path` when present.
- `synthesize_garden`: `draft_created`, `draft_id`, `capture_count`, `knowledge_link_count`.
- `revise_garden_draft`: `draft_updated`, `draft_id`.
- `commit_garden_draft`: `card_created`, `card_id`, `path`, `knowledge_link_count`.
- `search_my_notes`: `result_count`, `used_index`, `used_fallback`, `source_count`.
- `daily_review`: `review_item_id`, `object_type`, `view_count_incremented`.
- `review_followup`: `followup_answered`, `source_count`.

P0 should mark a run `incomplete` only when the workflow-specific required evidence is clearly absent. It should not mark low-result searches as incomplete if the envelope intentionally says no result was found.

## Task 1: Add Agent Run Schema And Repository

**Files:**

- Modify: `db/schema.py`
- Create: `db/agent_run_repo.py`
- Test: `tests/test_agent_run_repo.py`

- [ ] **Step 1: Write the failing repository lifecycle test**

Create `tests/test_agent_run_repo.py`:

```python
import json

from db.agent_run_repo import AgentRunRepo
from db.schema import init_db


def test_agent_run_repo_records_success_lifecycle(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    repo = AgentRunRepo(conn)

    run = repo.start_run(
        agent="garden",
        intent="synthesize_garden",
        trigger="wechat",
        input_ref={"capture_ids": ["c1", "c2"]},
    )
    repo.record_event(
        run["id"],
        event_type="tool_call",
        payload={"tool": "synthesize_garden"},
    )
    repo.complete_run(
        run["id"],
        output_ref={"draft_id": "d1"},
        evidence={"draft_created": True, "capture_count": 2},
        warnings=[],
    )

    latest = repo.latest(limit=1)[0]
    assert latest["id"] == run["id"]
    assert latest["status"] == "success"
    assert json.loads(latest["input_json"]) == {"capture_ids": ["c1", "c2"]}
    assert json.loads(latest["output_json"]) == {"draft_id": "d1"}
    assert json.loads(latest["evidence_json"])["draft_created"] is True

    events = repo.events(run["id"])
    assert len(events) == 1
    assert events[0]["event_type"] == "tool_call"


def test_agent_run_repo_marks_error(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    repo = AgentRunRepo(conn)

    run = repo.start_run(
        agent="rag",
        intent="search_knowledge_base",
        trigger="wechat",
        input_ref={"query": "AI"},
    )
    repo.fail_run(run["id"], errors=["boom"], evidence={"has_envelope": False})

    latest = repo.latest(limit=1)[0]
    assert latest["status"] == "error"
    assert json.loads(latest["errors_json"]) == ["boom"]
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_agent_run_repo.py -q
```

Expected: fail because `db.agent_run_repo` and the new tables do not exist.

- [ ] **Step 3: Add schema constants and initialize tables**

Modify `db/schema.py`:

```python
CREATE_AGENT_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    intent TEXT NOT NULL,
    trigger TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    errors_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
)
"""

CREATE_AGENT_RUN_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS agent_run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""
```

Inside `init_db`, after `CREATE_CHANGE_LOG_TABLE`, add:

```python
    conn.execute(CREATE_AGENT_RUNS_TABLE)
    conn.execute(CREATE_AGENT_RUN_EVENTS_TABLE)
```

- [ ] **Step 4: Implement the repository**

Create `db/agent_run_repo.py`:

```python
"""SQLite repository for Hermes Agent run ledger."""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any


class AgentRunRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def start_run(
        self,
        *,
        agent: str,
        intent: str,
        trigger: str = "",
        input_ref: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """
            INSERT INTO agent_runs
            (id, agent, intent, trigger, status, input_json, updated_at)
            VALUES (?, ?, ?, ?, 'running', ?, datetime('now'))
            """,
            (run_id, agent, intent, trigger, _json(input_ref or {})),
        )
        self.conn.commit()
        return dict(self.conn.execute(
            "SELECT * FROM agent_runs WHERE id = ?",
            (run_id,),
        ).fetchone())

    def record_event(
        self,
        run_id: str,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO agent_run_events (run_id, event_type, payload_json)
            VALUES (?, ?, ?)
            """,
            (run_id, event_type, _json(payload or {})),
        )
        self.conn.execute(
            "UPDATE agent_runs SET updated_at = datetime('now') WHERE id = ?",
            (run_id,),
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
        self.conn.execute(
            """
            UPDATE agent_runs
            SET status = ?,
                output_json = ?,
                evidence_json = ?,
                warnings_json = ?,
                updated_at = datetime('now'),
                completed_at = datetime('now')
            WHERE id = ?
            """,
            (
                status,
                _json(output_ref or {}),
                _json(evidence or {}),
                _json(warnings or []),
                run_id,
            ),
        )
        self.conn.commit()

    def fail_run(
        self,
        run_id: str,
        *,
        errors: list[str],
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE agent_runs
            SET status = 'error',
                errors_json = ?,
                evidence_json = ?,
                updated_at = datetime('now'),
                completed_at = datetime('now')
            WHERE id = ?
            """,
            (_json(errors), _json(evidence or {}), run_id),
        )
        self.conn.commit()

    def latest(
        self,
        *,
        limit: int = 10,
        agent: str = "",
        intent: str = "",
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM agent_runs"
        filters: list[str] = []
        params: list[Any] = []
        if agent:
            filters.append("agent = ?")
            params.append(agent)
        if intent:
            filters.append("intent = ?")
            params.append(intent)
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM agent_run_events WHERE run_id = ? ORDER BY id ASC",
                (run_id,),
            ).fetchall()
        ]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
```

- [ ] **Step 5: Run repository tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_agent_run_repo.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add db/schema.py db/agent_run_repo.py tests/test_agent_run_repo.py
git commit -m "feat: add agent run ledger repository"
```

## Task 2: Add Completion Evidence Extraction

**Files:**

- Create: `agents/run_evidence.py`
- Test: `tests/test_run_evidence.py`

- [ ] **Step 1: Write evidence tests**

Create `tests/test_run_evidence.py`:

```python
from agents.run_evidence import build_completion_evidence, output_ref_from_envelope


def test_garden_synthesize_evidence_detects_draft():
    envelope = {
        "status": "success",
        "agent": "garden",
        "intent": "synthesize_garden",
        "chat_preview": "ok",
        "durable_artifacts": [
            {"type": "garden_draft", "id": "d1", "title": "T", "status": "draft_ready"}
        ],
        "source_map": [],
        "next_actions": [],
        "warnings": [],
        "errors": [],
        "meta": {"capture_ids": ["c1", "c2"], "draft_id": "d1"},
    }

    evidence = build_completion_evidence(envelope)

    assert evidence["has_envelope"] is True
    assert evidence["draft_created"] is True
    assert evidence["draft_id"] == "d1"
    assert evidence["capture_count"] == 2


def test_commit_garden_evidence_detects_card_path():
    envelope = {
        "status": "success",
        "agent": "garden",
        "intent": "commit_garden_draft",
        "chat_preview": "ok",
        "durable_artifacts": [
            {
                "type": "garden_card",
                "id": "g1",
                "title": "T",
                "path": "garden/T-g1.md",
                "status": "seed",
            }
        ],
        "source_map": [],
        "next_actions": [],
        "warnings": [],
        "errors": [],
        "meta": {"card_id": "g1", "path": "garden/T-g1.md"},
    }

    evidence = build_completion_evidence(envelope)
    output = output_ref_from_envelope(envelope)

    assert evidence["card_created"] is True
    assert evidence["card_id"] == "g1"
    assert evidence["path"] == "garden/T-g1.md"
    assert output["artifacts"][0]["id"] == "g1"


def test_rag_evidence_allows_intentional_no_results():
    envelope = {
        "status": "success",
        "agent": "rag",
        "intent": "search_knowledge_base",
        "chat_preview": "知识库中没有找到相关信息。",
        "durable_artifacts": [],
        "source_map": [],
        "next_actions": [],
        "warnings": [],
        "errors": [],
        "meta": {"result_count": 0, "used_index": False, "used_fallback": False},
    }

    evidence = build_completion_evidence(envelope)

    assert evidence["result_count"] == 0
    assert evidence["search_completed"] is True
    assert evidence["required_evidence_present"] is True
```

- [ ] **Step 2: Run the failing evidence tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_run_evidence.py -q
```

Expected: fail because `agents.run_evidence` does not exist.

- [ ] **Step 3: Implement evidence extraction**

Create `agents/run_evidence.py`:

```python
"""Completion evidence helpers for Hermes knowledge Agent runs."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bridge.result_envelope import ENVELOPE_FIELDS


def build_completion_evidence(result: Mapping[str, Any]) -> dict[str, Any]:
    has_envelope = set(ENVELOPE_FIELDS).issubset(set(result.keys()))
    evidence: dict[str, Any] = {
        "has_envelope": has_envelope,
        "status": result.get("status", ""),
        "artifact_count": len(result.get("durable_artifacts") or []),
        "source_count": len(result.get("source_map") or []),
        "warning_count": len(result.get("warnings") or []),
        "error_count": len(result.get("errors") or []),
    }
    agent = str(result.get("agent", ""))
    intent = str(result.get("intent", ""))
    meta = result.get("meta") if isinstance(result.get("meta"), Mapping) else {}
    artifacts = result.get("durable_artifacts") or []

    if agent == "garden" and intent == "synthesize_garden":
        draft = _first_artifact(artifacts, "garden_draft")
        evidence.update({
            "draft_created": bool(draft),
            "draft_id": str(meta.get("draft_id") or (draft or {}).get("id") or ""),
            "capture_count": len(meta.get("capture_ids") or []),
            "knowledge_link_count": len(meta.get("knowledge_links") or []),
        })
    elif agent == "garden" and intent == "commit_garden_draft":
        card = _first_artifact(artifacts, "garden_card")
        evidence.update({
            "card_created": bool(card),
            "card_id": str(meta.get("card_id") or (card or {}).get("id") or ""),
            "path": str(meta.get("path") or (card or {}).get("path") or ""),
            "knowledge_link_count": len(meta.get("knowledge_links") or []),
        })
    elif agent == "garden" and intent == "revise_garden_draft":
        draft = _first_artifact(artifacts, "garden_draft")
        evidence.update({
            "draft_updated": bool(draft),
            "draft_id": str(meta.get("draft_id") or (draft or {}).get("id") or ""),
        })
    elif agent == "rag" and intent == "search_knowledge_base":
        evidence.update({
            "search_completed": result.get("status") == "success",
            "result_count": int(meta.get("result_count") or 0),
            "used_index": bool(meta.get("used_index")),
            "used_fallback": bool(meta.get("used_fallback")),
        })
    elif agent in {"librarian", "library"}:
        evidence.update(_library_evidence(meta))
    elif agent == "theme":
        evidence.update({
            "theme_scan_completed": result.get("status") == "success",
            "artifact_count": len(artifacts),
            "source_count": len(result.get("source_map") or []),
        })
    elif agent == "review":
        evidence.update({
            "review_completed": result.get("status") == "success",
            "review_item_id": str(meta.get("item_id") or meta.get("review_item_id") or ""),
            "object_type": str(meta.get("object_type") or ""),
        })

    evidence["required_evidence_present"] = _required_evidence_present(
        agent,
        intent,
        evidence,
    )
    return evidence


def output_ref_from_envelope(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifacts": list(result.get("durable_artifacts") or []),
        "sources": list(result.get("source_map") or []),
        "next_actions": list(result.get("next_actions") or []),
        "meta": dict(result.get("meta") or {}),
    }


def _first_artifact(artifacts: list[dict[str, Any]], artifact_type: str) -> dict[str, Any] | None:
    for artifact in artifacts:
        if artifact.get("type") == artifact_type:
            return artifact
    return None


def _library_evidence(meta: Mapping[str, Any]) -> dict[str, Any]:
    report = meta.get("report") if isinstance(meta.get("report"), Mapping) else meta
    return {
        "database_sync_checked": "database_sync" in report,
        "outlines_checked": "outlines" in report,
        "property_gaps_checked": "property_gaps" in report,
        "embedding_index_checked": "embedding_index" in report,
        "report_path": str(meta.get("report_path") or report.get("report_path") or ""),
    }


def _required_evidence_present(agent: str, intent: str, evidence: Mapping[str, Any]) -> bool:
    if evidence.get("status") == "error":
        return False
    if agent == "garden" and intent == "synthesize_garden":
        return bool(evidence.get("draft_created"))
    if agent == "garden" and intent == "commit_garden_draft":
        return bool(evidence.get("card_created") and evidence.get("path"))
    if agent == "garden" and intent == "revise_garden_draft":
        return bool(evidence.get("draft_updated"))
    if agent == "rag" and intent == "search_knowledge_base":
        return bool(evidence.get("search_completed"))
    if agent in {"librarian", "library"}:
        return bool(evidence.get("database_sync_checked") or evidence.get("outlines_checked"))
    if agent == "theme":
        return bool(evidence.get("theme_scan_completed"))
    if agent == "review":
        return bool(evidence.get("review_completed"))
    return bool(evidence.get("has_envelope") and evidence.get("status") == "success")
```

- [ ] **Step 4: Run evidence tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_run_evidence.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add agents/run_evidence.py tests/test_run_evidence.py
git commit -m "feat: derive agent completion evidence"
```

## Task 3: Add Bridge Run Ledger Wrapper

**Files:**

- Create: `bridge/run_ledger.py`
- Test: `tests/test_run_ledger.py`

- [ ] **Step 1: Write bridge wrapper tests**

Create `tests/test_run_ledger.py`:

```python
import json

from bridge.run_ledger import run_with_ledger
from db.agent_run_repo import AgentRunRepo
from db.schema import init_db


def test_run_with_ledger_records_success(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    repo = AgentRunRepo(conn)

    def work():
        return {
            "status": "success",
            "agent": "garden",
            "intent": "synthesize_garden",
            "chat_preview": "ok",
            "durable_artifacts": [
                {"type": "garden_draft", "id": "d1", "title": "T", "status": "draft_ready"}
            ],
            "source_map": [],
            "next_actions": [],
            "warnings": [],
            "errors": [],
            "meta": {"draft_id": "d1", "capture_ids": ["c1"]},
        }

    result = run_with_ledger(
        repo=repo,
        agent="garden",
        intent="synthesize_garden",
        trigger="wechat",
        input_ref={"capture_ids": ["c1"]},
        fn=work,
    )

    assert result["status"] == "success"
    run = repo.latest(limit=1)[0]
    assert run["status"] == "success"
    assert json.loads(run["evidence_json"])["draft_created"] is True


def test_run_with_ledger_marks_incomplete_when_evidence_missing(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    repo = AgentRunRepo(conn)

    def work():
        return {
            "status": "success",
            "agent": "garden",
            "intent": "commit_garden_draft",
            "chat_preview": "ok",
            "durable_artifacts": [],
            "source_map": [],
            "next_actions": [],
            "warnings": [],
            "errors": [],
            "meta": {},
        }

    run_with_ledger(
        repo=repo,
        agent="garden",
        intent="commit_garden_draft",
        trigger="wechat",
        input_ref={"draft_id": "d1"},
        fn=work,
    )

    run = repo.latest(limit=1)[0]
    assert run["status"] == "incomplete"
```

- [ ] **Step 2: Run the failing wrapper tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_run_ledger.py -q
```

Expected: fail because `bridge.run_ledger` does not exist.

- [ ] **Step 3: Implement the wrapper**

Create `bridge/run_ledger.py`:

```python
"""Bridge helper for recording Hermes Agent runs."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents.run_evidence import build_completion_evidence, output_ref_from_envelope
from db.agent_run_repo import AgentRunRepo


def run_with_ledger(
    *,
    repo: AgentRunRepo,
    agent: str,
    intent: str,
    trigger: str,
    input_ref: dict[str, Any],
    fn: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    run = repo.start_run(
        agent=agent,
        intent=intent,
        trigger=trigger,
        input_ref=input_ref,
    )
    repo.record_event(run["id"], event_type="started", payload=input_ref)
    try:
        result = fn()
    except Exception as exc:
        repo.fail_run(
            run["id"],
            errors=[f"{exc.__class__.__name__}: {exc}"],
            evidence={"has_envelope": False},
        )
        raise

    evidence = build_completion_evidence(result)
    status = "success" if evidence.get("required_evidence_present") else "incomplete"
    if result.get("status") == "error":
        status = "error"
    repo.complete_run(
        run["id"],
        output_ref=output_ref_from_envelope(result),
        evidence=evidence,
        warnings=list(result.get("warnings") or []),
        status=status,
    )
    repo.record_event(
        run["id"],
        event_type="completed",
        payload={"status": status, "evidence": evidence},
    )
    return result
```

- [ ] **Step 4: Run wrapper tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_run_ledger.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add bridge/run_ledger.py tests/test_run_ledger.py
git commit -m "feat: record bridge runs with completion evidence"
```

## Task 4: Instrument Knowledge Bridges

**Files:**

- Modify: `bridge/librarian_bridge.py`
- Modify: `bridge/theme_bridge.py`
- Modify: `bridge/garden_bridge.py`
- Modify: `bridge/rag_bridge.py`
- Modify: `bridge/review_bridge.py`
- Test: existing bridge tests plus small assertions where practical.

- [ ] **Step 1: Add ledger repository construction in each bridge**

In each bridge function, after config/repo initialization, create:

```python
from db.agent_run_repo import AgentRunRepo
from bridge.run_ledger import run_with_ledger
```

Then use:

```python
ledger = AgentRunRepo(repo.conn)
```

For functions that already create `CaptureRepo(init_db(config.db_path))`, reuse that connection. For `librarian_bridge`, use the same `init_db(config.db_path)` connection used by the library report.

- [ ] **Step 2: Wrap `synthesize_garden`**

In `bridge/garden_bridge.py`, keep the current body in a private helper:

```python
def _synthesize_garden_impl(capture_ids=None, question_id="", title="") -> dict:
    # existing synthesize_garden body moves here unchanged
```

Then make public `synthesize_garden` do:

```python
def synthesize_garden(capture_ids: list[str] = None, question_id: str = "", title: str = "") -> dict:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    return run_with_ledger(
        repo=AgentRunRepo(repo.conn),
        agent="garden",
        intent="synthesize_garden",
        trigger="wechat",
        input_ref={
            "capture_ids": capture_ids or [],
            "question_id": question_id,
            "title": title,
        },
        fn=lambda: _synthesize_garden_impl(capture_ids, question_id, title),
    )
```

- [ ] **Step 3: Wrap `revise_garden_draft` and `commit_garden_draft`**

Use the same pattern:

```python
return run_with_ledger(
    repo=AgentRunRepo(repo.conn),
    agent="garden",
    intent="commit_garden_draft",
    trigger="wechat",
    input_ref={"draft_id": draft_id},
    fn=lambda: _commit_garden_draft_impl(draft_id),
)
```

Use intent `revise_garden_draft` for revision.

- [ ] **Step 4: Wrap `run_theme_scan`**

In `bridge/theme_bridge.py`, wrap the public function:

```python
return run_with_ledger(
    repo=AgentRunRepo(repo.conn),
    agent="theme",
    intent="theme_scan",
    trigger="wechat",
    input_ref={"rescan_all": rescan_all},
    fn=lambda: _run_theme_scan_impl(rescan_all=rescan_all),
)
```

- [ ] **Step 5: Wrap RAG search**

In `bridge/rag_bridge.py`, after `_init()` returns config and repo:

```python
return run_with_ledger(
    repo=AgentRunRepo(repo.conn),
    agent="rag",
    intent="search_knowledge_base",
    trigger="wechat",
    input_ref={"query": query},
    fn=lambda: _search_knowledge_base_impl(query),
)
```

Move the current body into `_search_knowledge_base_impl`.

- [ ] **Step 6: Wrap Review bridge functions**

In `bridge/review_bridge.py`, wrap `run_daily_review`, `review_followup`, and `review_feedback` if they return standard envelopes. Use:

```python
agent="review"
trigger="wechat"
```

Use the current function name as the intent.

- [ ] **Step 7: Add one focused integration assertion**

Extend a nearby Garden bridge test to assert that a run row exists after successful synthesis:

```python
rows = conn.execute(
    "SELECT agent, intent, status, evidence_json FROM agent_runs ORDER BY created_at DESC LIMIT 1"
).fetchall()
assert rows[0]["agent"] == "garden"
assert rows[0]["intent"] == "synthesize_garden"
assert rows[0]["status"] == "success"
```

- [ ] **Step 8: Run focused bridge tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_garden_bridge.py tests/test_theme_bridge.py tests/test_rag_bridge.py tests/test_review_agent.py -q
```

Expected: pass.

- [ ] **Step 9: Commit**

```bash
git add bridge/librarian_bridge.py bridge/theme_bridge.py bridge/garden_bridge.py bridge/rag_bridge.py bridge/review_bridge.py tests/test_garden_bridge.py
git commit -m "feat: record knowledge agent bridge runs"
```

## Task 5: Add Read-Only Run Status Bridge And Hermes Tool

**Files:**

- Create: `bridge/agent_run_bridge.py`
- Create: `/Users/pipi/Downloads/hermes-agent-main/tools/agent_run_status_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py`
- Test: `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_agent_run_status_tool.py`

- [ ] **Step 1: Write Hermes custom bridge**

Create `bridge/agent_run_bridge.py`:

```python
"""Read-only bridge for Hermes Agent run ledger."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.capture_agent import load_config
from bridge.result_envelope import success_envelope
from db.agent_run_repo import AgentRunRepo
from db.schema import init_db


def show_agent_runs(agent: str = "", intent: str = "", limit: int = 5) -> dict[str, Any]:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = AgentRunRepo(init_db(config.db_path))
    rows = repo.latest(limit=max(1, min(limit, 20)), agent=agent, intent=intent)
    lines = ["最近 Agent 运行记录："]
    if not rows:
        lines.append("暂无记录。")
    for row in rows:
        evidence = _loads(row["evidence_json"], {})
        summary = _summarize_evidence(row["agent"], row["intent"], evidence)
        lines.append(
            f"- {row['created_at']} {row['agent']}.{row['intent']} "
            f"[{row['status']}] {summary}"
        )
    return success_envelope(
        agent="system",
        intent="show_agent_runs",
        chat_preview="\n".join(lines),
        meta={"runs": [dict(row) for row in rows]},
    )


def _loads(text: str, default):
    try:
        return json.loads(text or "")
    except json.JSONDecodeError:
        return default


def _summarize_evidence(agent: str, intent: str, evidence: dict[str, Any]) -> str:
    if agent == "garden" and intent == "synthesize_garden":
        return f"draft={evidence.get('draft_id', '-')}, captures={evidence.get('capture_count', 0)}"
    if agent == "garden" and intent == "commit_garden_draft":
        return f"card={evidence.get('card_id', '-')}, path={evidence.get('path', '-')}"
    if agent == "rag":
        return f"results={evidence.get('result_count', 0)}, sources={evidence.get('source_count', 0)}"
    if agent == "theme":
        return f"artifacts={evidence.get('artifact_count', 0)}, sources={evidence.get('source_count', 0)}"
    if agent in {"librarian", "library"}:
        return "checked DB/files/outlines/index"
    if agent == "review":
        return f"item={evidence.get('review_item_id', '-')}"
    return f"artifacts={evidence.get('artifact_count', 0)}, sources={evidence.get('source_count', 0)}"
```

- [ ] **Step 2: Add runtime tool**

Create `/Users/pipi/Downloads/hermes-agent-main/tools/agent_run_status_tool.py`:

```python
"""Hermes tool: show_agent_runs - 查询最近知识库 Agent 运行记录."""
from __future__ import annotations

import sys

HERMES_CUSTOM = "/Users/pipi/hermes-custom"
if HERMES_CUSTOM not in sys.path:
    sys.path.insert(0, HERMES_CUSTOM)

from bridge.agent_run_bridge import show_agent_runs
from bridge.result_envelope import envelope_json
from tools.hermes_custom_envelope import envelope_error_json
from tools.registry import registry

SHOW_AGENT_RUNS_SCHEMA = {
    "name": "show_agent_runs",
    "description": (
        "查询最近 Hermes 知识库 Agent 运行记录。"
        "用户问“刚才有没有跑通”“哪个工具被调用了”“最近 Agent 做了什么”时使用。"
        "这是只读工具，不会修改知识库。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "可选过滤：garden/theme/rag/review/librarian",
            },
            "intent": {
                "type": "string",
                "description": "可选过滤：例如 synthesize_garden, theme_scan, search_knowledge_base",
            },
            "limit": {
                "type": "integer",
                "description": "返回条数，默认 5，最大 20",
            },
        },
        "required": [],
    },
}


def _handler(args: dict, **kw) -> str:
    try:
        result = show_agent_runs(
            agent=args.get("agent", ""),
            intent=args.get("intent", ""),
            limit=int(args.get("limit", 5) or 5),
        )
        return envelope_json(result)
    except Exception as exc:
        return envelope_error_json(agent="system", intent="show_agent_runs", exc=exc)


registry.register(
    name=SHOW_AGENT_RUNS_SCHEMA["name"],
    schema=SHOW_AGENT_RUNS_SCHEMA,
    handler=_handler,
    toolset="personal-assistant",
)
```

- [ ] **Step 3: Add runtime test**

Create `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_agent_run_status_tool.py`:

```python
from tools.agent_run_status_tool import SHOW_AGENT_RUNS_SCHEMA


def test_show_agent_runs_schema_is_read_only():
    assert SHOW_AGENT_RUNS_SCHEMA["name"] == "show_agent_runs"
    assert "只读" in SHOW_AGENT_RUNS_SCHEMA["description"]
    assert "agent" in SHOW_AGENT_RUNS_SCHEMA["parameters"]["properties"]
```

- [ ] **Step 4: Update toolset expectation**

Modify `/Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py` so `show_agent_runs` is included in the `personal-assistant` toolset expectation.

- [ ] **Step 5: Run runtime tests**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_agent_run_status_tool.py /Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py -q
```

Expected: pass.

- [ ] **Step 6: Commit runtime changes**

```bash
cd /Users/pipi/Downloads/hermes-agent-main
git add tools/agent_run_status_tool.py tests/tools/test_agent_run_status_tool.py tests/test_toolsets.py
git commit -m "feat: expose agent run status tool"
```

## Task 6: Add Knowledge Pipeline Smoke Check

**Files:**

- Create: `scripts/check_agent_runs.py`
- Test: `tests/test_check_agent_runs.py`

- [ ] **Step 1: Write smoke report test**

Create `tests/test_check_agent_runs.py`:

```python
from db.agent_run_repo import AgentRunRepo
from db.schema import init_db
from scripts.check_agent_runs import build_agent_run_health


def test_build_agent_run_health_flags_incomplete_runs(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    repo = AgentRunRepo(conn)
    run = repo.start_run(
        agent="garden",
        intent="commit_garden_draft",
        trigger="test",
        input_ref={"draft_id": "d1"},
    )
    repo.complete_run(
        run["id"],
        status="incomplete",
        output_ref={},
        evidence={"card_created": False},
    )

    report = build_agent_run_health(repo)

    assert report["incomplete_count"] == 1
    assert report["error_count"] == 0
    assert report["healthy"] is False
```

- [ ] **Step 2: Implement the script**

Create `scripts/check_agent_runs.py`:

```python
"""Print a compact health report for recent Hermes Agent runs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.capture_agent import load_config
from db.agent_run_repo import AgentRunRepo
from db.schema import init_db


def build_agent_run_health(repo: AgentRunRepo, *, limit: int = 50) -> dict[str, Any]:
    rows = repo.latest(limit=limit)
    incomplete = [row for row in rows if row["status"] == "incomplete"]
    errors = [row for row in rows if row["status"] == "error"]
    running = [row for row in rows if row["status"] == "running"]
    return {
        "healthy": not incomplete and not errors and not running,
        "checked": len(rows),
        "incomplete_count": len(incomplete),
        "error_count": len(errors),
        "running_count": len(running),
        "problem_runs": [
            {
                "id": row["id"],
                "agent": row["agent"],
                "intent": row["intent"],
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in [*incomplete, *errors, *running]
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Hermes Agent run ledger health.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    config = load_config(args.config)
    repo = AgentRunRepo(init_db(config.db_path))
    print(json.dumps(build_agent_run_health(repo, limit=args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run smoke report tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_check_agent_runs.py -q
```

Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add scripts/check_agent_runs.py tests/test_check_agent_runs.py
git commit -m "feat: add agent run health check"
```

## Task 7: Full Verification And Hermes Restart

**Files:**

- No new files.
- Verify both repositories.

- [ ] **Step 1: Run Hermes custom focused tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  tests/test_agent_run_repo.py \
  tests/test_run_evidence.py \
  tests/test_run_ledger.py \
  tests/test_check_agent_runs.py \
  tests/test_garden_bridge.py \
  tests/test_theme_bridge.py \
  tests/test_rag_bridge.py \
  tests/test_review_agent.py \
  -q
```

Expected: pass.

- [ ] **Step 2: Run Hermes custom full tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/ -q
```

Expected: pass, with existing skipped tests unchanged.

- [ ] **Step 3: Run Hermes runtime focused tests**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' \
  /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_agent_run_status_tool.py \
  /Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py \
  /Users/pipi/Downloads/hermes-agent-main/tests/agent/test_hermes_custom_direct_reply.py \
  -q
```

Expected: pass.

- [ ] **Step 4: Restart Hermes with session cleanup**

Run:

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="/Users/pipi/.hermes/session_backups/$stamp"
mkdir -p "$backup_dir" /Users/pipi/.hermes/logs /Users/pipi/.hermes/sessions
cp -a /Users/pipi/.hermes/sessions/. "$backup_dir/" 2>/dev/null || true
find /Users/pipi/.hermes/sessions -mindepth 1 -maxdepth 1 -delete 2>/dev/null || true
pkill -f "[h]ermes_cli.main gateway run" 2>/dev/null || true
nohup /Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m hermes_cli.main gateway run --replace > "/Users/pipi/.hermes/logs/gateway-restart-$stamp.log" 2>&1 &
sleep 6
pgrep -fl "hermes_cli.main gateway run"
cat /Users/pipi/.hermes/gateway_state.json
```

Expected:

- One active gateway process.
- `gateway_state.json` exists.
- WeChat connection status is available.

- [ ] **Step 5: Manual WeChat smoke test**

Ask in WeChat:

```text
从最近几篇 AI 相关笔记里提炼一个可以放进 Garden 的观点
```

Then confirm:

```text
存入花园
```

Then ask:

```text
刚才 Agent 跑通了吗
```

Expected:

- Garden first returns a draft.
- `存入花园` commits a Garden Card.
- `show_agent_runs` returns recent `garden.synthesize_garden` and `garden.commit_garden_draft` entries with `success` status and draft/card evidence.

- [ ] **Step 6: Push both branches**

Run in `hermes-custom`:

```bash
git status --short
git push
```

Run in `hermes-agent-main`:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
git status --short
git push
```

Expected: both current branches pushed, with unrelated Life Assistant work in `hermes-custom` left untouched.

## Rollback Plan

If the ledger causes runtime issues:

1. Keep the schema tables. Empty tables do not affect existing workflows.
2. Remove bridge wrapper calls and call the original private implementations directly.
3. Temporarily remove `show_agent_runs` from `personal-assistant`.
4. Restart Hermes with session cleanup.

The rollback should not delete user data, captures, raw files, Garden cards, Theme pages, or Review items.

## Success Criteria

- Every P0 knowledge Agent run records one `agent_runs` row.
- Successful Garden draft and commit runs include durable draft/card evidence.
- RAG runs record source counts and intentional no-result searches as completed.
- Library and Theme runs record enough evidence to answer whether they actually checked or changed anything.
- The user can ask in WeChat whether the last Agent run succeeded and receive a concise, evidence-based answer.
- Existing knowledge Agent behavior remains unchanged except for observability.

## Self-Review

- Spec coverage: Covers the Memex-inspired lifecycle idea, evidence-based completion, task persistence, read-only status query, tests, and Hermes restart discipline.
- Placeholder scan: No deferred placeholders remain; every task has concrete files, commands, and expected outcomes.
- Scope check: This is one focused observability upgrade. It does not redesign Capture, Garden synthesis quality, Theme clustering, Review selection, or Life Assistant.
- Type consistency: `agent_runs`, `agent_run_events`, `AgentRunRepo`, `build_completion_evidence`, `run_with_ledger`, and `show_agent_runs` are used consistently across tasks.
