---
status: current
owner: knowledge-service
last_verified: 2026-11-17
verified_against: local check in .tmp/worker-e2e
---

# Folder write jobs

Opt-in background queue for **folder renames**. It exists so bulk or scheduled
renames can be retried and audited, without adding a second synchronous write
path to the HTTP API.

## Scope

- Supported intent: `rename_folder`.
- The HTTP endpoint `PATCH /api/library/folders/{path}` still renames
  synchronously (`rename_topic` + `rename_stored_folder`). The queue is not
  wired into that endpoint.
- If you enqueue a rename, do not also call the endpoint for the same folder
  while the job is pending.
- Delete, merge and archive intents are **not** implemented. Do not add a
  submit helper for them until the executor and tests exist.

## Pieces

| Piece | File |
|---|---|
| Job table | `db/schema.py` → `knowledge_write_jobs` |
| Job repository | `db/write_job_repo.py` |
| Submission / status helpers | `bridge/knowledge_job_submit.py` |
| Domain service (single writer) | `agents/folder_service.py` |
| Worker | `workers/knowledge_write_worker.py` |
| Launcher | `start_worker.py` |

## Start the worker

Windows:

```powershell
cd <repos>\hermes-custom
.venv\Scripts\python.exe start_worker.py
```

macOS/Linux:

```bash
cd <repos>/hermes-custom
.venv/bin/python start_worker.py
```

Process one batch and exit (useful for a cron job or a smoke check):

```powershell
.venv\Scripts\python.exe -m workers.knowledge_write_worker --config config.yaml
```

Defaults come from `config.yaml`:

```yaml
worker:
  poll_interval_seconds: 2.0
  max_retry_count: 3
  batch_size: 10
```

## Submit a job

```python
from bridge.knowledge_job_submit import submit_folder_rename_job
from db.schema import init_db

conn = init_db("data/captures.db")
job_id = submit_folder_rename_job("AI工具", "AI产品", conn)
print(job_id)
```

Wait for completion when the caller needs the result:

```python
from bridge.knowledge_job_submit import wait_for_job

status = wait_for_job(job_id, conn, timeout_seconds=30)
```

`wait_for_job` raises `TimeoutError` when no worker picks the job up, and
`RuntimeError` when the job ends `failed` or `repair_needed`.

## What a rename updates

`agents.folder_service.rename_folder` writes all of these, then commits:

1. `capture_drafts.capture_intent.folder_path` for uncommitted drafts.
2. Capture Markdown frontmatter `folder_path` (the source of truth).
3. `captures.updated_at`.
4. `capture_metadata.folder_path` (the indexed mirror).
5. `garden_cards.source_theme` where it matches the old folder.
6. `<vault>/.hermes/knowledge_folders.json` (the Library folder list).

Missing capture files and unreadable drafts become warnings, not failures. A
write error rolls the whole job back.

## Job states

| State | Meaning |
|---|---|
| `pending` | Queued, not picked up yet |
| `running` | Worker is executing it |
| `retrying` | Failed once; the worker will pick it up again |
| `completed` | All stores updated |
| `failed` | Validation or payload error; no retry |
| `repair_needed` | Retry budget exhausted; inspect by hand |

Inspect with:

```sql
SELECT id, intent, status, step, error, retry_count, created_at
FROM knowledge_write_jobs
ORDER BY created_at DESC
LIMIT 20;
```

## Tests

```powershell
.venv\Scripts\python.exe -m pytest tests\test_folder_service.py tests\test_knowledge_write_jobs.py -q
```

The suite covers the domain service (drafts, Markdown, metadata mirror, cards,
folder registry) and the worker (success path, retry pickup, partial failure,
timeout).
