# Hermes Knowledge Stabilization Baseline

Date: 2026-06-19

Scope: Batch 1 from `docs/superpowers/plans/2026-06-19-hermes-knowledge-stabilization-roadmap.md`.

## Git State

Branch:

```text
data-repair-audit...origin/data-repair-audit [ahead 18]
```

The worktree already contained many uncommitted code, test, script, and data changes before Batch 1 started. Batch 1 must not revert or clean those changes.

## Test Baseline

Command:

```bash
cd /Users/pipi/hermes-custom
.venv/bin/python -m pytest tests/ -q
```

Result:

```text
576 passed, 2 skipped in 17.85s
```

## Runtime Surface Baseline

- `hermes-custom` owns the Knowledge capability layer and FastAPI API on `127.0.0.1:8000`.
- Hermes runtime lives at `/Users/pipi/Downloads/hermes-agent-main`.
- The user-facing Hermes dashboard is expected from the runtime dashboard, normally `http://127.0.0.1:9119/`.
- The legacy Next dashboard in `dashboard/` is deprecated; `dashboard/package.json` intentionally exits for `dev`, `build`, and `start`.

## Phase 0 Status

Phase 0 is complete. No business logic was changed for this baseline.
