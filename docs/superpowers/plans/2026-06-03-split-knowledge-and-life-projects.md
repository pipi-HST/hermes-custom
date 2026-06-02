# Split Hermes Knowledge And Life Projects Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the current mixed Hermes Knowledge + Life Assistant code into two versioned capability projects while keeping one Hermes gateway/runtime.

**Architecture:** `/Users/pipi/hermes-custom` remains the Knowledge project: Capture, Library, RAG, Theme, Garden, Review, Canvas, Obsidian, and knowledge dashboard. A new `/Users/pipi/hermes-life` project owns Life Assistant code, tests, data repair scripts, dashboard page, and Life docs. `/Users/pipi/Downloads/hermes-agent-main` remains the single gateway/runtime and imports Life tools from the new Life package.

**Tech Stack:** Python 3, SQLite, FastAPI, pytest, Next.js dashboard files, Hermes Agent Result Envelope JSON contract, local Git repositories.

---

## Assumptions And Boundaries

- There will be one Hermes gateway/runtime, not two WeChat gateways.
- Knowledge data and Life data stay isolated. This keeps the ADR in `/Users/pipi/hermes-custom/docs/adr/0002-isolate-life-and-knowledge-contexts.md` true after the split.
- Real local config files, SQLite DBs, logs, repair reports, and backups are not committed.
- The first split favors a direct local package boundary over a premature shared-common package. If a second capability project later needs the same helpers, create `hermes-common` then.
- Do not delete Life code from `/Users/pipi/hermes-custom` until `/Users/pipi/hermes-life` tests pass and `hermes-agent-main` can import the new Life package.
- Keep existing runtime behavior. This split should not redesign Life reminder policy, memory policy, or Knowledge workflows.

## Success Criteria

- `/Users/pipi/hermes-life` exists as a standalone Git repo with its own tests passing.
- `/Users/pipi/hermes-custom` no longer contains Life Assistant implementation, Life tests, Life dashboard page, or Life-only docs, and Knowledge tests still pass.
- `/Users/pipi/Downloads/hermes-agent-main` Life tools import from `/Users/pipi/hermes-life` instead of `/Users/pipi/hermes-custom`.
- Hermes still exposes `record_life_event` and `life_tick` through the existing personal-assistant toolset.
- Knowledge workflows still run without Life schema migration or Life API routes.
- Life workflows still run against isolated `life_*` tables.

## File Inventory

### Move To `/Users/pipi/hermes-life`

Core Python files:

- `/Users/pipi/hermes-custom/agents/life_agent.py`
- `/Users/pipi/hermes-custom/agents/life_intent_parser.py`
- `/Users/pipi/hermes-custom/agents/life_memory.py`
- `/Users/pipi/hermes-custom/bridge/life_bridge.py`
- `/Users/pipi/hermes-custom/db/life_repo.py`
- `/Users/pipi/hermes-custom/db/life_schema.py`
- `/Users/pipi/hermes-custom/api/routes/life.py`

Scripts:

- `/Users/pipi/hermes-custom/scripts/life_acceptance.py`
- `/Users/pipi/hermes-custom/scripts/life_audit.py`
- `/Users/pipi/hermes-custom/scripts/life_repair.py`
- `/Users/pipi/hermes-custom/scripts/life_smoke.py`
- `/Users/pipi/hermes-custom/scripts/life_tick_cron.py`

Tests:

- `/Users/pipi/hermes-custom/tests/test_life_agent.py`
- `/Users/pipi/hermes-custom/tests/test_life_api.py`
- `/Users/pipi/hermes-custom/tests/test_life_bridge.py`
- `/Users/pipi/hermes-custom/tests/test_life_intent_parser.py`
- `/Users/pipi/hermes-custom/tests/test_life_memory.py`
- `/Users/pipi/hermes-custom/tests/test_life_repo.py`
- `/Users/pipi/hermes-custom/tests/test_life_scenarios.py`
- `/Users/pipi/hermes-custom/tests/test_life_smoke_script.py`

Dashboard files:

- `/Users/pipi/hermes-custom/dashboard/src/lib/lifeApi.ts`
- `/Users/pipi/hermes-custom/dashboard/src/pages/life.tsx`

Life docs:

- `/Users/pipi/hermes-custom/docs/superpowers/specs/2026-05-29-life-assistant-design.md`
- `/Users/pipi/hermes-custom/docs/superpowers/specs/2026-05-30-life-dashboard-design.md`
- `/Users/pipi/hermes-custom/docs/superpowers/specs/2026-05-31-life-weekly-dashboard-and-push-report-design.md`
- `/Users/pipi/hermes-custom/docs/superpowers/specs/2026-06-01-life-memory-influence-trace-design.md`
- `/Users/pipi/hermes-custom/docs/superpowers/specs/2026-06-01-life-memory-layer-design.md`
- `/Users/pipi/hermes-custom/docs/superpowers/specs/2026-06-02-life-p2-enhancement-plan.md`
- `/Users/pipi/hermes-custom/docs/superpowers/plans/2026-05-30-life-dashboard.md`
- `/Users/pipi/hermes-custom/docs/superpowers/plans/2026-05-30-life-intent-parser-refactor.md`
- `/Users/pipi/hermes-custom/docs/superpowers/plans/2026-05-31-life-weekly-dashboard-and-push-report.md`
- `/Users/pipi/hermes-custom/docs/superpowers/plans/2026-06-01-life-decision-trace-and-flexible-recording.md`
- `/Users/pipi/hermes-custom/docs/superpowers/plans/2026-06-02-life-reminder-digest-and-sedentary-hourly.md`
- `/Users/pipi/hermes-custom/docs/superpowers/reports/2026-05-31-life-closeout-audit.md`
- `/Users/pipi/hermes-custom/docs/superpowers/reports/2026-05-31-life-current-state.md`
- `/Users/pipi/hermes-custom/docs/superpowers/reports/2026-05-31-life-pipeline-audit.md`
- `/Users/pipi/hermes-custom/docs/superpowers/reports/2026-06-02-life-acceptance.md`
- `/Users/pipi/hermes-custom/docs/superpowers/reports/2026-06-02-life-p2-enhancement-completion.md`
- `/Users/pipi/hermes-custom/docs/superpowers/reports/2026-06-02-life-pipeline-acceptance-summary.md`

Do not move `/Users/pipi/hermes-custom/docs/superpowers/plans/2026-05-31-garden-agent-draft-lifecycle.md`; it matched `life` only because `lifecycle` contains that substring.

### Keep In `/Users/pipi/hermes-custom`

- Knowledge agents: `capture_agent.py`, `library_agent.py`, `rag_agent.py`, `theme_agent.py`, `garden_agent.py`, `review_agent.py`, `canvas_agent.py`, and related graph/index helpers.
- Knowledge bridges: `capture_bridge.py`, `rag_bridge.py`, `theme_bridge.py`, `garden_bridge.py`, `canvas_bridge.py`, `review_bridge.py`, `librarian_bridge.py`.
- Knowledge DB schema and repo files.
- Knowledge API route `/Users/pipi/hermes-custom/api/routes/captures.py`.
- Obsidian vault sync, repair, export, capture, RAG, Theme, Garden, Review, and Canvas tests.
- Knowledge portfolio and interview docs.

### Modify In `/Users/pipi/Downloads/hermes-agent-main`

- `/Users/pipi/Downloads/hermes-agent-main/tools/record_life_event_tool.py`
- `/Users/pipi/Downloads/hermes-agent-main/tools/life_tick_tool.py`
- `/Users/pipi/Downloads/hermes-agent-main/toolsets.py` only if tool registration paths change.
- `/Users/pipi/Downloads/hermes-agent-main/tests/agent/test_hermes_custom_direct_reply.py` only if test import assumptions need adjustment.
- Add focused tests if the import-path helper is introduced.

## New Life Project Layout

Create:

```text
/Users/pipi/hermes-life/
  .gitignore
  README.md
  config.example.yaml
  pyproject.toml
  hermes_life/
    __init__.py
    agents/
      __init__.py
      life_agent.py
      life_intent_parser.py
      life_memory.py
    api/
      __init__.py
      routes/
        __init__.py
        life.py
    bridge/
      __init__.py
      life_bridge.py
      result_envelope.py
    db/
      __init__.py
      life_repo.py
      life_schema.py
    scripts/
      __init__.py
      life_acceptance.py
      life_audit.py
      life_repair.py
      life_smoke.py
      life_tick_cron.py
  dashboard/
    src/
      lib/
        lifeApi.ts
      pages/
        life.tsx
  docs/
    adr/
      0001-life-project-boundaries.md
    superpowers/
      plans/
      reports/
      specs/
  tests/
    test_life_agent.py
    test_life_api.py
    test_life_bridge.py
    test_life_intent_parser.py
    test_life_memory.py
    test_life_repo.py
    test_life_scenarios.py
    test_life_smoke_script.py
```

## Task 1: Create Life Repo Skeleton

**Files:**

- Create: `/Users/pipi/hermes-life/.gitignore`
- Create: `/Users/pipi/hermes-life/README.md`
- Create: `/Users/pipi/hermes-life/config.example.yaml`
- Create: `/Users/pipi/hermes-life/pyproject.toml`
- Create: `/Users/pipi/hermes-life/hermes_life/__init__.py`

- [ ] **Step 1: Create directories**

Run:

```bash
mkdir -p /Users/pipi/hermes-life/hermes_life/{agents,api/routes,bridge,db,scripts}
mkdir -p /Users/pipi/hermes-life/dashboard/src/{lib,pages}
mkdir -p /Users/pipi/hermes-life/docs/{adr,superpowers/plans,superpowers/reports,superpowers/specs}
mkdir -p /Users/pipi/hermes-life/tests
```

Expected: directories exist and no files from `hermes-custom` are deleted.

- [ ] **Step 2: Add project metadata**

Create `/Users/pipi/hermes-life/pyproject.toml`:

```toml
[project]
name = "hermes-life"
version = "0.1.0"
description = "Hermes Life Assistant capability project"
requires-python = ">=3.11"
dependencies = [
  "fastapi",
  "pyyaml",
  "requests",
]

[project.optional-dependencies]
test = [
  "pytest",
  "httpx",
]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

Create `/Users/pipi/hermes-life/.gitignore`:

```gitignore
.DS_Store
__pycache__/
*.py[cod]
.pytest_cache/
.venv/
venv/
env/
config.yaml
.env
.env.*
data/*.db
data/*.db-*
data/*.log
data/backups/
data/reports/
dashboard/node_modules/
dashboard/.next/
dashboard/*.tsbuildinfo
```

Create `/Users/pipi/hermes-life/config.example.yaml`:

```yaml
database:
  path: "/Users/pipi/hermes-life/data/life.db"

llm:
  provider: deepseek
  model: deepseek-v4-pro
  base_url: "https://api.deepseek.com"
  api_key: ""

life:
  notifications_enabled: false
  prefer_llm_for_life_records: true
```

Create `/Users/pipi/hermes-life/README.md`:

```markdown
# Hermes Life Assistant

Hermes Life Assistant is the lifestyle-management capability project for Hermes.

It owns:

- `life_*` SQLite tables
- Life reminder ticks
- Life records and long-term decisions
- Life memory observations
- Life dashboard/API

It does not own:

- Obsidian captures
- RAG, Theme, Garden, Review, Canvas, or Library workflows
- Hermes gateway/runtime transport code
```

- [ ] **Step 3: Initialize Git**

Run:

```bash
cd /Users/pipi/hermes-life
git init
git checkout -b codex/split-life-project
```

Expected: repo initialized on `codex/split-life-project`.

## Task 2: Copy Life Files Into Package

**Files:**

- Copy from `/Users/pipi/hermes-custom/agents/life_*.py` to `/Users/pipi/hermes-life/hermes_life/agents/`
- Copy from `/Users/pipi/hermes-custom/bridge/life_bridge.py` to `/Users/pipi/hermes-life/hermes_life/bridge/life_bridge.py`
- Copy from `/Users/pipi/hermes-custom/db/life_*.py` to `/Users/pipi/hermes-life/hermes_life/db/`
- Copy from `/Users/pipi/hermes-custom/api/routes/life.py` to `/Users/pipi/hermes-life/hermes_life/api/routes/life.py`
- Copy from `/Users/pipi/hermes-custom/scripts/life_*.py` to `/Users/pipi/hermes-life/hermes_life/scripts/`
- Copy from `/Users/pipi/hermes-custom/tests/test_life_*.py` to `/Users/pipi/hermes-life/tests/`

- [ ] **Step 1: Copy files without deleting originals**

Run:

```bash
cp /Users/pipi/hermes-custom/agents/life_agent.py /Users/pipi/hermes-life/hermes_life/agents/life_agent.py
cp /Users/pipi/hermes-custom/agents/life_intent_parser.py /Users/pipi/hermes-life/hermes_life/agents/life_intent_parser.py
cp /Users/pipi/hermes-custom/agents/life_memory.py /Users/pipi/hermes-life/hermes_life/agents/life_memory.py
cp /Users/pipi/hermes-custom/bridge/life_bridge.py /Users/pipi/hermes-life/hermes_life/bridge/life_bridge.py
cp /Users/pipi/hermes-custom/db/life_repo.py /Users/pipi/hermes-life/hermes_life/db/life_repo.py
cp /Users/pipi/hermes-custom/db/life_schema.py /Users/pipi/hermes-life/hermes_life/db/life_schema.py
cp /Users/pipi/hermes-custom/api/routes/life.py /Users/pipi/hermes-life/hermes_life/api/routes/life.py
cp /Users/pipi/hermes-custom/scripts/life_acceptance.py /Users/pipi/hermes-life/hermes_life/scripts/life_acceptance.py
cp /Users/pipi/hermes-custom/scripts/life_audit.py /Users/pipi/hermes-life/hermes_life/scripts/life_audit.py
cp /Users/pipi/hermes-custom/scripts/life_repair.py /Users/pipi/hermes-life/hermes_life/scripts/life_repair.py
cp /Users/pipi/hermes-custom/scripts/life_smoke.py /Users/pipi/hermes-life/hermes_life/scripts/life_smoke.py
cp /Users/pipi/hermes-custom/scripts/life_tick_cron.py /Users/pipi/hermes-life/hermes_life/scripts/life_tick_cron.py
cp /Users/pipi/hermes-custom/tests/test_life_*.py /Users/pipi/hermes-life/tests/
```

Expected: original `hermes-custom` files remain unchanged.

- [ ] **Step 2: Add package init files**

Create empty files:

```bash
touch /Users/pipi/hermes-life/hermes_life/__init__.py
touch /Users/pipi/hermes-life/hermes_life/agents/__init__.py
touch /Users/pipi/hermes-life/hermes_life/api/__init__.py
touch /Users/pipi/hermes-life/hermes_life/api/routes/__init__.py
touch /Users/pipi/hermes-life/hermes_life/bridge/__init__.py
touch /Users/pipi/hermes-life/hermes_life/db/__init__.py
touch /Users/pipi/hermes-life/hermes_life/scripts/__init__.py
```

Expected: Python can import `hermes_life`.

## Task 3: Make Life Package Independent

**Files:**

- Modify: `/Users/pipi/hermes-life/hermes_life/agents/life_agent.py`
- Modify: `/Users/pipi/hermes-life/hermes_life/agents/life_memory.py`
- Modify: `/Users/pipi/hermes-life/hermes_life/bridge/life_bridge.py`
- Modify: `/Users/pipi/hermes-life/hermes_life/db/life_schema.py`
- Modify: `/Users/pipi/hermes-life/hermes_life/api/routes/life.py`
- Modify: `/Users/pipi/hermes-life/hermes_life/scripts/life_*.py`
- Modify: `/Users/pipi/hermes-life/tests/test_life_*.py`
- Create: `/Users/pipi/hermes-life/hermes_life/bridge/result_envelope.py`

- [ ] **Step 1: Add local envelope helper**

Create `/Users/pipi/hermes-life/hermes_life/bridge/result_envelope.py`:

```python
"""Agent Result Envelope helpers used by Hermes Life tools."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

ENVELOPE_FIELDS = (
    "status",
    "agent",
    "intent",
    "chat_preview",
    "durable_artifacts",
    "source_map",
    "next_actions",
    "warnings",
    "errors",
    "meta",
)


def success_envelope(
    *,
    agent: str,
    intent: str,
    chat_preview: str,
    durable_artifacts: list[dict[str, Any]] | None = None,
    source_map: list[dict[str, Any]] | None = None,
    next_actions: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _envelope(
        status="success",
        agent=agent,
        intent=intent,
        chat_preview=chat_preview,
        durable_artifacts=durable_artifacts,
        source_map=source_map,
        next_actions=next_actions,
        warnings=warnings,
        errors=[],
        meta=meta,
    )


def error_envelope(
    *,
    agent: str,
    intent: str,
    error: str,
    chat_preview: str | None = None,
    warnings: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _envelope(
        status="error",
        agent=agent,
        intent=intent,
        chat_preview=chat_preview or error,
        durable_artifacts=[],
        source_map=[],
        next_actions=[],
        warnings=warnings,
        errors=[error],
        meta=meta,
    )


def artifact_ref(
    *,
    type: str,
    id: str,
    title: str,
    path: str = "",
    status: str = "",
) -> dict[str, str]:
    return {
        "type": type,
        "id": id,
        "title": title,
        "path": path,
        "status": status,
    }


def envelope_json(envelope: Mapping[str, Any]) -> str:
    require_envelope(envelope)
    return json.dumps(dict(envelope), ensure_ascii=False)


def require_envelope(value: Mapping[str, Any]) -> Mapping[str, Any]:
    missing = [field for field in ENVELOPE_FIELDS if field not in value]
    if missing:
        raise ValueError(f"Envelope missing fields: {', '.join(missing)}")
    return value


def _envelope(
    *,
    status: str,
    agent: str,
    intent: str,
    chat_preview: str,
    durable_artifacts: list[dict[str, Any]] | None,
    source_map: list[dict[str, Any]] | None,
    next_actions: list[dict[str, Any]] | None,
    warnings: list[str] | None,
    errors: list[str] | None,
    meta: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "status": status,
        "agent": agent,
        "intent": intent,
        "chat_preview": chat_preview,
        "durable_artifacts": durable_artifacts or [],
        "source_map": source_map or [],
        "next_actions": next_actions or [],
        "warnings": warnings or [],
        "errors": errors or [],
        "meta": meta or {},
    }
```

- [ ] **Step 2: Replace package imports**

Run:

```bash
cd /Users/pipi/hermes-life
python - <<'PY'
from pathlib import Path

replacements = {
    "from agents.life_agent": "from hermes_life.agents.life_agent",
    "from agents.life_intent_parser": "from hermes_life.agents.life_intent_parser",
    "from agents.life_memory": "from hermes_life.agents.life_memory",
    "from bridge.life_bridge": "from hermes_life.bridge.life_bridge",
    "from bridge.result_envelope": "from hermes_life.bridge.result_envelope",
    "from db.life_repo": "from hermes_life.db.life_repo",
    "from db.life_schema": "from hermes_life.db.life_schema",
    "from scripts.life_acceptance": "from hermes_life.scripts.life_acceptance",
    "from scripts.life_audit": "from hermes_life.scripts.life_audit",
    "from scripts.life_repair": "from hermes_life.scripts.life_repair",
    "from scripts.life_smoke": "from hermes_life.scripts.life_smoke",
    "from scripts.life_tick_cron": "from hermes_life.scripts.life_tick_cron",
    "from scripts import life_tick_cron": "from hermes_life.scripts import life_tick_cron",
}

for path in [*Path("hermes_life").rglob("*.py"), *Path("tests").rglob("*.py")]:
    text = path.read_text()
    updated = text
    for old, new in replacements.items():
        updated = updated.replace(old, new)
    if updated != text:
        path.write_text(updated)
PY
```

Expected: Life package files import `hermes_life.*`, not top-level `agents`, `bridge`, `db`, or `scripts`.

- [ ] **Step 3: Make DB init Life-only**

Modify `/Users/pipi/hermes-life/hermes_life/db/life_schema.py` so `init_life_db()` no longer imports Knowledge `db.schema.init_db`.

Replace:

```python
from db.schema import init_db
```

with no import, then replace `init_life_db()` with:

```python
def init_life_db(db_path: str) -> sqlite3.Connection:
    """Initialize a Life Assistant SQLite database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    migrate_life_schema(conn)
    return conn
```

Expected: `/Users/pipi/hermes-life` can create Life tables without Knowledge schema.

- [ ] **Step 4: Make bridge config local to Life**

Modify `/Users/pipi/hermes-life/hermes_life/bridge/life_bridge.py`:

Remove:

```python
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
```

Set:

```python
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_config_path = PROJECT_ROOT / "config.yaml"
```

In `_load_db_path()`, replace default path:

```python
return cfg.get("database", {}).get("path", str(PROJECT_ROOT / "data" / "life.db"))
```

In `_load_life_config()`, replace default log path:

```python
default_log_path = PROJECT_ROOT / "data" / "life.log"
```

Expected: Life bridge resolves config and data paths from `/Users/pipi/hermes-life`.

- [ ] **Step 5: Run Life tests**

Run:

```bash
cd /Users/pipi/hermes-life
python -m pytest tests/test_life_agent.py tests/test_life_bridge.py tests/test_life_repo.py tests/test_life_intent_parser.py tests/test_life_memory.py tests/test_life_scenarios.py tests/test_life_smoke_script.py tests/test_life_api.py -q
```

Expected: all Life tests pass. If imports fail, fix only package import paths and rerun.

- [ ] **Step 6: Commit new Life project checkpoint**

Run:

```bash
cd /Users/pipi/hermes-life
git add .
git commit -m "feat: extract Hermes Life Assistant project"
```

Expected: clean Life repo with passing tests.

## Task 4: Move Life Dashboard Files

**Files:**

- Already copied: `/Users/pipi/hermes-life/dashboard/src/lib/lifeApi.ts`
- Already copied: `/Users/pipi/hermes-life/dashboard/src/pages/life.tsx`
- Later delete from: `/Users/pipi/hermes-custom/dashboard/src/lib/lifeApi.ts`
- Later delete from: `/Users/pipi/hermes-custom/dashboard/src/pages/life.tsx`

- [ ] **Step 1: Copy dashboard files**

Run:

```bash
cp /Users/pipi/hermes-custom/dashboard/src/lib/lifeApi.ts /Users/pipi/hermes-life/dashboard/src/lib/lifeApi.ts
cp /Users/pipi/hermes-custom/dashboard/src/pages/life.tsx /Users/pipi/hermes-life/dashboard/src/pages/life.tsx
```

Expected: Life dashboard source exists in the new Life repo.

- [ ] **Step 2: Record dashboard limitation**

Add this note to `/Users/pipi/hermes-life/README.md`:

```markdown
## Dashboard

The Life dashboard source is kept here so Life UI work is versioned with Life logic.
The first split does not create a separate Next.js app yet. A later step can either
mount this page into the existing Knowledge dashboard shell or create a dedicated
Life dashboard app if the user wants separate local UIs.
```

Expected: split is honest about UI ownership without forcing a second frontend process now.

## Task 5: Move Life Docs

**Files:**

- Copy Life specs/plans/reports listed in the inventory.
- Create: `/Users/pipi/hermes-life/docs/adr/0001-life-project-boundaries.md`

- [ ] **Step 1: Copy Life docs**

Run:

```bash
cp /Users/pipi/hermes-custom/docs/superpowers/specs/2026-05-29-life-assistant-design.md /Users/pipi/hermes-life/docs/superpowers/specs/
cp /Users/pipi/hermes-custom/docs/superpowers/specs/2026-05-30-life-dashboard-design.md /Users/pipi/hermes-life/docs/superpowers/specs/
cp /Users/pipi/hermes-custom/docs/superpowers/specs/2026-05-31-life-weekly-dashboard-and-push-report-design.md /Users/pipi/hermes-life/docs/superpowers/specs/
cp /Users/pipi/hermes-custom/docs/superpowers/specs/2026-06-01-life-memory-influence-trace-design.md /Users/pipi/hermes-life/docs/superpowers/specs/
cp /Users/pipi/hermes-custom/docs/superpowers/specs/2026-06-01-life-memory-layer-design.md /Users/pipi/hermes-life/docs/superpowers/specs/
cp /Users/pipi/hermes-custom/docs/superpowers/specs/2026-06-02-life-p2-enhancement-plan.md /Users/pipi/hermes-life/docs/superpowers/specs/
cp /Users/pipi/hermes-custom/docs/superpowers/plans/2026-05-30-life-dashboard.md /Users/pipi/hermes-life/docs/superpowers/plans/
cp /Users/pipi/hermes-custom/docs/superpowers/plans/2026-05-30-life-intent-parser-refactor.md /Users/pipi/hermes-life/docs/superpowers/plans/
cp /Users/pipi/hermes-custom/docs/superpowers/plans/2026-05-31-life-weekly-dashboard-and-push-report.md /Users/pipi/hermes-life/docs/superpowers/plans/
cp /Users/pipi/hermes-custom/docs/superpowers/plans/2026-06-01-life-decision-trace-and-flexible-recording.md /Users/pipi/hermes-life/docs/superpowers/plans/
cp /Users/pipi/hermes-custom/docs/superpowers/plans/2026-06-02-life-reminder-digest-and-sedentary-hourly.md /Users/pipi/hermes-life/docs/superpowers/plans/
cp /Users/pipi/hermes-custom/docs/superpowers/reports/2026-05-31-life-closeout-audit.md /Users/pipi/hermes-life/docs/superpowers/reports/
cp /Users/pipi/hermes-custom/docs/superpowers/reports/2026-05-31-life-current-state.md /Users/pipi/hermes-life/docs/superpowers/reports/
cp /Users/pipi/hermes-custom/docs/superpowers/reports/2026-05-31-life-pipeline-audit.md /Users/pipi/hermes-life/docs/superpowers/reports/
cp /Users/pipi/hermes-custom/docs/superpowers/reports/2026-06-02-life-acceptance.md /Users/pipi/hermes-life/docs/superpowers/reports/
cp /Users/pipi/hermes-custom/docs/superpowers/reports/2026-06-02-life-p2-enhancement-completion.md /Users/pipi/hermes-life/docs/superpowers/reports/
cp /Users/pipi/hermes-custom/docs/superpowers/reports/2026-06-02-life-pipeline-acceptance-summary.md /Users/pipi/hermes-life/docs/superpowers/reports/
```

Expected: Life docs are available in the new repo. Garden lifecycle docs stay in Knowledge repo.

- [ ] **Step 2: Add Life boundary ADR**

Create `/Users/pipi/hermes-life/docs/adr/0001-life-project-boundaries.md`:

```markdown
# Hermes Life Assistant project boundaries

Hermes Life Assistant is an independent capability project plugged into the shared Hermes runtime.

It may share:

- Hermes delivery infrastructure
- Agent Result Envelope JSON shape
- Local model credentials through local config

It must not read or create:

- Obsidian captures
- Knowledge themes
- Garden cards
- Review items
- Capture drafts

Knowledge tooling must not read Life lifestyle records, reminder logs, or Life memories.
```

Expected: boundary is documented in the new project.

## Task 6: Update Hermes Runtime Life Tools

**Files:**

- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/record_life_event_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/life_tick_tool.py`
- Test: `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_life_tools_imports.py`

- [ ] **Step 1: Add failing import-path test**

Create `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_life_tools_imports.py`:

```python
from __future__ import annotations

import importlib
import sys


def test_life_tools_use_hermes_life_project_path():
    sys.modules.pop("tools.record_life_event_tool", None)
    sys.modules.pop("tools.life_tick_tool", None)

    record_tool = importlib.import_module("tools.record_life_event_tool")
    tick_tool = importlib.import_module("tools.life_tick_tool")

    assert "/Users/pipi/hermes-life" in record_tool.sys.path
    assert "/Users/pipi/hermes-life" in tick_tool.sys.path
```

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest tests/tools/test_life_tools_imports.py -q
```

Expected before modification: FAIL because tools still insert `/Users/pipi/hermes-custom`.

- [ ] **Step 2: Update runtime imports**

Modify `/Users/pipi/Downloads/hermes-agent-main/tools/record_life_event_tool.py`:

```python
import sys
sys.path.insert(0, "/Users/pipi/hermes-life")

from hermes_life.bridge.life_bridge import record_life_event as do_record_life_event
from hermes_life.bridge.result_envelope import envelope_json
```

Modify `/Users/pipi/Downloads/hermes-agent-main/tools/life_tick_tool.py`:

```python
import sys
sys.path.insert(0, "/Users/pipi/hermes-life")

from hermes_life.bridge.life_bridge import life_tick as do_life_tick
from hermes_life.bridge.result_envelope import envelope_json
```

Keep:

```python
from tools.hermes_custom_envelope import envelope_error_json
from tools.registry import registry
```

Expected: gateway still returns valid envelopes, but Life code comes from `/Users/pipi/hermes-life`.

- [ ] **Step 3: Run runtime tool tests**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest tests/tools/test_life_tools_imports.py tests/agent/test_hermes_custom_direct_reply.py -q
```

Expected: tests pass.

- [ ] **Step 4: Commit runtime update**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
git status --short
git add tools/record_life_event_tool.py tools/life_tick_tool.py tests/tools/test_life_tools_imports.py
git commit -m "chore: load Life tools from Hermes Life project"
```

Expected: runtime repo has a focused commit.

## Task 7: Remove Life From Knowledge Project

**Files:**

- Modify: `/Users/pipi/hermes-custom/api/main.py`
- Delete Life files listed under "Move To `/Users/pipi/hermes-life`"
- Keep: `/Users/pipi/hermes-custom/docs/adr/0002-isolate-life-and-knowledge-contexts.md`

- [ ] **Step 1: Add Knowledge API regression test**

Modify or add a focused API test in `/Users/pipi/hermes-custom/tests/test_api_main.py`:

```python
from __future__ import annotations

from api.main import create_app


def test_create_app_does_not_mount_life_routes():
    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/api/captures" in paths
    assert "/api/life/summary" not in paths
```

Run:

```bash
cd /Users/pipi/hermes-custom
.venv/bin/python -m pytest tests/test_api_main.py -q
```

Expected before modification: FAIL because Life routes are still mounted.

- [ ] **Step 2: Remove Life imports and route mounting**

Modify `/Users/pipi/hermes-custom/api/main.py`.

Remove:

```python
from db.life_schema import migrate_life_schema
from api.routes.life import create_life_router
```

Remove:

```python
migrate_life_schema(conn)
```

Remove:

```python
app.include_router(create_life_router(db_path=db_path), prefix="/api")
```

Expected: Knowledge API only initializes Knowledge schema and capture routes.

- [ ] **Step 3: Delete Life implementation from Knowledge repo**

Run:

```bash
cd /Users/pipi/hermes-custom
rm agents/life_agent.py agents/life_intent_parser.py agents/life_memory.py
rm bridge/life_bridge.py
rm db/life_repo.py db/life_schema.py
rm api/routes/life.py
rm scripts/life_acceptance.py scripts/life_audit.py scripts/life_repair.py scripts/life_smoke.py scripts/life_tick_cron.py
rm tests/test_life_agent.py tests/test_life_api.py tests/test_life_bridge.py tests/test_life_intent_parser.py tests/test_life_memory.py tests/test_life_repo.py tests/test_life_scenarios.py tests/test_life_smoke_script.py
rm dashboard/src/lib/lifeApi.ts dashboard/src/pages/life.tsx
```

Expected: deleted files are already present in `/Users/pipi/hermes-life`.

- [ ] **Step 4: Move Life docs out of Knowledge repo**

Run:

```bash
cd /Users/pipi/hermes-custom
rm docs/superpowers/specs/2026-05-29-life-assistant-design.md
rm docs/superpowers/specs/2026-05-30-life-dashboard-design.md
rm docs/superpowers/specs/2026-05-31-life-weekly-dashboard-and-push-report-design.md
rm docs/superpowers/specs/2026-06-01-life-memory-influence-trace-design.md
rm docs/superpowers/specs/2026-06-01-life-memory-layer-design.md
rm docs/superpowers/specs/2026-06-02-life-p2-enhancement-plan.md
rm docs/superpowers/plans/2026-05-30-life-dashboard.md
rm docs/superpowers/plans/2026-05-30-life-intent-parser-refactor.md
rm docs/superpowers/plans/2026-05-31-life-weekly-dashboard-and-push-report.md
rm docs/superpowers/plans/2026-06-01-life-decision-trace-and-flexible-recording.md
rm docs/superpowers/plans/2026-06-02-life-reminder-digest-and-sedentary-hourly.md
rm docs/superpowers/reports/2026-05-31-life-closeout-audit.md
rm docs/superpowers/reports/2026-05-31-life-current-state.md
rm docs/superpowers/reports/2026-05-31-life-pipeline-audit.md
rm docs/superpowers/reports/2026-06-02-life-acceptance.md
rm docs/superpowers/reports/2026-06-02-life-p2-enhancement-completion.md
rm docs/superpowers/reports/2026-06-02-life-pipeline-acceptance-summary.md
```

Expected: `docs/superpowers/plans/2026-05-31-garden-agent-draft-lifecycle.md` remains.

- [ ] **Step 5: Run Knowledge tests**

Run:

```bash
cd /Users/pipi/hermes-custom
.venv/bin/python -m pytest tests/ -q
```

Expected: Knowledge tests pass. If test collection imports Life files, update only those tests or docs references.

- [ ] **Step 6: Commit Knowledge cleanup**

Run:

```bash
cd /Users/pipi/hermes-custom
git status --short
git add -A
git commit -m "chore: remove Life Assistant from Knowledge project"
```

Expected: focused Knowledge repo commit.

## Task 8: Runtime Smoke Test And Restart Hygiene

**Files:**

- Runtime logs under `~/.hermes/logs/`
- Runtime sessions under `~/.hermes/sessions`
- Existing restart scripts or manual process commands already used in this project

- [ ] **Step 1: Back up and clear Hermes sessions**

Run:

```bash
mkdir -p ~/.hermes/session_backups
if [ -d ~/.hermes/sessions ]; then
  mv ~/.hermes/sessions ~/.hermes/session_backups/sessions-$(date +%Y%m%dT%H%M%S)
fi
mkdir -p ~/.hermes/sessions
```

Expected: old conversation state is not used in smoke tests.

- [ ] **Step 2: Restart Hermes**

Use the project’s existing Hermes restart flow. After restart, verify:

```bash
ps aux | rg 'hermes|gateway' | rg -v rg
tail -n 120 ~/.hermes/logs/*.log
```

Expected: one active API/gateway path, no import error for `hermes_life`.

- [ ] **Step 3: Smoke test Life tool import directly**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python - <<'PY'
from tools.record_life_event_tool import _handler as record_handler
from tools.life_tick_tool import _handler as tick_handler

print(record_handler({"message": "午饭吃了牛肉饭"}))
print(tick_handler({"dry_run": True}))
PY
```

Expected: both outputs are JSON envelopes with `"agent": "life"`.

- [ ] **Step 4: Smoke test Knowledge still imports**

Run:

```bash
cd /Users/pipi/hermes-custom
.venv/bin/python - <<'PY'
from api.main import create_app

app = create_app()
paths = {route.path for route in app.routes}
print("/api/captures" in paths)
print(any(path.startswith("/api/life") for path in paths))
PY
```

Expected:

```text
True
False
```

## Task 9: Push Repositories

**Files:**

- Git remotes for `/Users/pipi/hermes-life`
- Git remotes for `/Users/pipi/hermes-custom`
- Git remotes for `/Users/pipi/Downloads/hermes-agent-main`

- [ ] **Step 1: Create GitHub repo for Life project if needed**

Run:

```bash
cd /Users/pipi/hermes-life
git remote -v
```

If no remote exists and `gh auth status` succeeds:

```bash
gh repo create hermes-life --private --source=/Users/pipi/hermes-life --remote=origin --push
```

Expected: `hermes-life` pushed to GitHub.

- [ ] **Step 2: Push Knowledge and runtime commits**

Run:

```bash
cd /Users/pipi/hermes-custom
git push

cd /Users/pipi/Downloads/hermes-agent-main
git push
```

Expected: all split commits are on their remotes.

## Rollback Strategy

- If `/Users/pipi/hermes-life` tests fail before deleting Life from Knowledge, stop and keep `hermes-custom` unchanged.
- If Knowledge tests fail after removal, revert the Knowledge cleanup commit only:

```bash
cd /Users/pipi/hermes-custom
git revert <knowledge-cleanup-commit>
```

- If runtime import fails, revert the runtime import commit only:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
git revert <runtime-import-commit>
```

- If Life repo extraction fails, delete or archive `/Users/pipi/hermes-life` and retry from the clean `hermes-custom` source.

## Self-Review

- Spec coverage: The plan covers new Life repo creation, package independence, runtime import updates, Knowledge cleanup, testing, restart hygiene, and GitHub push.
- Placeholder scan: No `TBD`, `TODO`, or open-ended test instructions are required for the first implementation pass.
- Type consistency: The package name is consistently `hermes_life`; the repo path is consistently `/Users/pipi/hermes-life`; the existing gateway remains `/Users/pipi/Downloads/hermes-agent-main`.
- Risk note: The only intentional duplication is `result_envelope.py`. This is acceptable for the first split because it avoids creating a fourth repo before the boundary is proven.
