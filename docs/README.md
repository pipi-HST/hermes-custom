# Documentation Index

## Current

- `../README.md` — project entry and quickstart
- `../ARCHITECTURE.md` — ownership, data truth, write paths
- `../GLOSSARY.md` — canonical terms
- `../RUNBOOK.md` — start/stop/troubleshoot/reset
- `../AGENTS.md` — rules for AI agents and developers
- `../CONTEXT.md` — product workflow and domain language
- `CURRENT-STATE.md` — generated test/table/commit counts
- `adr/` — architecture decisions (0001-0004 implemented; 0005 deferred)
- `PRODUCT-REFACTOR-STORY.md` — product narrative / interview material
- `CODE-REVIEW-V3.md` — 2026-11-16 review snapshot
- `PLAN-P10-T9-T26.md` — current technical-debt execution plan
- `BENCHMARK-RAG-V3-*.md` — benchmark records; read each file's warning first
- `local-data-reset-runbook.md` — destructive reset procedure

## Historical

Everything under `history/` is archival:

- `history/HANDOFF-2026-05.md` — old handoff, superseded
- `history/plans/` — 50 old implementation plans (`status: historical`)
- `history/specs/` — 10 old design specs (`status: historical`)
- `history/reports/` — old status reports and the pre-existing improvement report
- `superpowers/demos/` — old HTML demos
- `checkpoints/` — old code snapshots; **never restore over current code**

## Rules

- Current docs need `status`, `owner`, `last_verified`, `verified_against`.
- Historical docs keep their original content but must say `status: historical`.
- Do not hand-write counts; run `python scripts/update_current_state.py`.
- If a current doc disagrees with code, fix the doc or the code in the same change.
