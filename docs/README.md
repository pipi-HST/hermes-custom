---
status: current
owner: knowledge-service
last_verified: 2026-11-17
verified_against: "e4cfdda (hermes-custom) / ceac73d (hermes-agent-main) + 2026-11-17 working tree"
---

# Documentation Index

Every `docs/*.md` file must appear below; `scripts/check_docs.py` fails otherwise.

## Current

- `../README.md` — project entry and quickstart
- `../ARCHITECTURE.md` — ownership, data truth, write paths
- `../GLOSSARY.md` — canonical terms
- `../RUNBOOK.md` — start/stop/troubleshoot/reset
- `../AGENTS.md` — rules for AI agents and developers
- `../CONTEXT.md` — product workflow and domain language
- `../CONTEXT-MAP.md` — bounded contexts and ownership
- `../SETUP.md` — supplementary setup notes
- `CURRENT-STATE.md` — generated test/table counts (banner is the status)
- `INVENTORY.md` — generated routes / table writers / local-only routes
- `capture-intent.md` — CaptureIntent fields, allowed values and readers
- `adr/` — architecture decisions (0001-0004 implemented; 0005 deferred)
- `folder-write-jobs.md` — opt-in folder-rename worker (not wired to the API)
- `PRODUCT-REFACTOR-STORY.md` — product narrative / interview material
- `local-data-reset-runbook.md` — destructive reset procedure

## Snapshots (status: historical)

Kept for context; do not use as current runbooks.

- `CODE-REVIEW-V3.md` — 2026-11-16 red-team review
- `AUDIT-2026-11-17-doc-code-consistency.md` — 2026-11-17 doc/code audit
- `EXECUTION-PLAN-V3.3.md` — plan that closed the V3 review
- `PLAN-P10-T9-T26.md` — P10/T9/T26 debt plan (completed 2026-11-17)
- `BENCHMARK-RAG-V3.md`, `BENCHMARK-RAG-V3-dashscope.md`,
  `BENCHMARK-RAG-V3-offline-postfix.md` — benchmark records; read each
  file's warning first
- `hermes-project-map.md`, `hermes-function-tool-map-2026-06-01.md`,
  `hermes-knowledge-v0.1-acceptance.md` — old maps and acceptance snapshot

## Historical

Everything under `history/` is archival:

- `history/HANDOFF-2026-05.md` — old handoff, superseded
- `history/plans/` — 50 old implementation plans (`status: historical`)
- `history/specs/` — 10 old design specs (`status: historical`)
- `history/reports/` — old status reports and the pre-existing improvement report
- `superpowers/demos/` — old HTML demos
- `checkpoints/` — old code snapshots; **never restore over current code**

## Rules

- Every doc carries `status`; current docs also need `owner`, `last_verified`,
  `verified_against`.
- Historical docs keep their original content but must say `status: historical`.
- Do not hand-write counts; run `python scripts/update_current_state.py`.
- If a current doc disagrees with code, fix the doc or the code in the same change.
- `python scripts/check_docs.py` enforces links, absolute paths, headers, and
  this index.
