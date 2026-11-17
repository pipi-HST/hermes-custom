---
status: current
owner: knowledge-service
last_verified: 2026-11-18
verified_against: "3e6b974 + 2026-11-18 parallel fix branches"
---

# Schema version and migrations

`db/schema.py` owns the SQLite schema.  The database carries its version in
`PRAGMA user_version`; `SCHEMA_VERSION` is the version this checkout expects.

Current version: **4**.

## What each version means

| Version | Change |
|---|---|
| 1 | Initial captures/garden/draft schema. |
| 2 | Garden v2 payload, normalized timestamps, FTS map. |
| 3 | Capture metadata, reading sessions/drafts, write jobs, confirmation tokens, graph edges. |
| 4 | Removed `captures.embedding`, `captures.review_*` and garden v1 mirror columns; embeddings live only in `capture_embeddings`; card content in `garden_cards.payload_json` with the `content_markdown` column as a maintained cache. |

The exact DDL lives in `db/schema.py`; this table is the human summary.

## How `init_db()` works

1. Open the connection, enable WAL/foreign keys.
2. If `PRAGMA user_version >= SCHEMA_VERSION` and `captures` exists, take the
   **fast path**: skip the full DDL batch, but still run idempotent recovery
   and backfill guards (interrupted `captures_new` swap, garden content
   backfill, legacy column drops, indexes, FTS integrity, timestamp
   normalization).
3. Otherwise run the full DDL batch and all `_migrate_*` helpers inside one
   transaction, then set `PRAGMA user_version = SCHEMA_VERSION`.
4. Commit.

There is no per-version chain such as `migrate_v3_to_v4()` and no down
migration.  The helpers are idempotent and converge from any older shape; this
is deliberate for a single-user SQLite database and is tested by setting
`user_version` to 1/2 and re-running `init_db`.

## Adding a version

1. Bump `SCHEMA_VERSION`.
2. Add the DDL or a new `_migrate_*` helper to the full-batch path.
3. Make the helper safe to run twice.
4. Add a test in `tests/test_schema.py` that starts from the previous
   `user_version`, runs `init_db`, and asserts the new shape and
   `PRAGMA user_version`.
5. Update this document and `docs/CURRENT-STATE.md` if the table count changes.
6. Do not edit production `data/captures.db` by hand; back it up and let
   `init_db` migrate it.

## Known limits

- The fast path still runs a few backfills; the comment in `db/schema.py`
  names them.  They are cheap guards, not a full migration.
- There is no rollback.  Take a file backup before a destructive repair.
- `scripts/repair_data.py` handles row-level drift; schema migrations handle
  shape changes.  Do not mix them.
