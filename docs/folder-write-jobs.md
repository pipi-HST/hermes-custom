---
status: current
owner: knowledge-service
last_verified: 2026-11-18
verified_against: "working tree (fix/kc-data, based on 3e6b974)"
---

# Folder write jobs

Opt-in background queue for **folder renames**. It exists so renames can be
retried and audited, and so the HTTP API uses the same validated domain service
as batch or scheduled work.

## Scope

- Supported intent: `rename_folder`.
- `PATCH /api/library/folders/{path}` has two modes, selected by
  `worker.enabled` in `config.yaml`:
  - `false` (default): rename synchronously through `rename_topic` +
    `rename_stored_folder`. No job row is created.
  - `true`: submit a `rename_folder` job and wait for the worker
    (`submit_folder_rename_job` + `wait_for_job`). The worker process must
    already be running; see below.
- Do not call the endpoint for a folder whose rename job is still pending: in
  worker mode the endpoint waits, but a second client can still queue a
  conflicting rename.
- Delete, merge and archive intents are **not** implemented. Do not add a
  submit helper for them until the executor and tests exist.

## Pieces

| Piece | File |
|---|---|
| Job table | `db/schema.py` → `knowledge_write_jobs` |
| Job repository | `db/write_job_repo.py` |
| Submission / status helpers | `bridge/knowledge_job_submit.py` |
| Domain service (job path) | `agents/folder_service.py` |
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
  enabled: false                 # PATCH /library/folders: sync when false
  rename_timeout_seconds: 30.0   # HTTP wait budget in worker mode
  poll_interval_seconds: 2.0
  max_retry_count: 3
  batch_size: 10
```

## Failure and timeout behaviour

With `worker.enabled: true` the HTTP endpoint submits the job and waits up to
`worker.rename_timeout_seconds`:

- `503` `worker_unavailable`: the job did not finish in time, usually because
  no worker is running. The response includes `job_id`; start the worker and
  inspect/retry the job. It is not a silent success.
- `500` `folder_rename_failed`: the worker ran the job and it ended `failed`
  or `repair_needed`; the response includes the job id and the error text.
- `200` only after the job is `completed`, so the returned folder list already
  reflects the rename.

With `worker.enabled: false` (default) no job row exists and errors are the
same synchronous `rename_topic` errors as before.

## Submit a job

The HTTP endpoint does this for you when `worker.enabled: true`. To enqueue one
directly (batch tooling, smoke check):

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

Missing capture files and unreadable drafts become warnings, not failures. The
SQLite updates (drafts, `captures.updated_at`, `capture_metadata`,
`garden_cards`) and the folder registry run after the Markdown writes; the DB
part is one transaction. A crash between the file writes and the DB commit can
leave the note on the new folder and the mirror on the old one. Rerun
`scripts/repair_topics.py` after a crash; that is the recovery path, not a
claim of all-or-nothing rollback.

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
