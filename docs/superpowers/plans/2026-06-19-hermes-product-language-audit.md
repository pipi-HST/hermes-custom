# Hermes Product Language Audit

Date: 2026-06-19

Scope: Phase 2 from `docs/superpowers/plans/2026-06-19-hermes-knowledge-stabilization-roadmap.md`.

## Active Context Updates

- `CONTEXT.md` now defines the user-facing product workflow:
  - material or question;
  - AI reads or discusses with sources;
  - draft is produced;
  - user reviews and confirms;
  - durable knowledge is written;
  - knowledge can be cited and reused later.
- `CONTEXT.md` now separates user-facing vocabulary from implementation vocabulary.
- `Knowledge Card` is the default user-facing term.
- `Garden Card` remains as a legacy/internal term for code, database tables, and the `garden/` directory.
- `CONTEXT-MAP.md` now describes Knowledge as a sourced workflow instead of a list of internal object names.

## Classification of Existing Terms

The repository still contains many references to Garden, Theme, MOC, Canvas, Capture Review, and discussion-note language. These are classified as follows:

- `CONTEXT.md` and `CONTEXT-MAP.md`: active context files; updated in this phase.
- `docs/superpowers/specs/` and older `docs/superpowers/plans/`: historical design records; references are allowed unless the document is being promoted as current runbook.
- `docs/superpowers/plans/2026-06-15-theme-garden-moc-executable-plan.md`: current execution history for Theme/Garden/MOC behavior; references are implementation-specific and acceptable.
- `dashboard/`: deprecated prototype UI; references are historical unless the dashboard is revived.
- Code, tests, and database names: implementation terms; do not rename in Phase 2.

## Product Rule

User-facing surfaces should prefer:

- Capture
- Reading Session
- Draft
- Knowledge Card
- Source

Internal and advanced surfaces may still use:

- Garden
- Theme
- MOC
- Canvas
- Run Ledger
- embedding index

Do not rename database tables, file directories, or public bridge functions just to match product copy. Rename only when a later phase creates an explicit migration plan.
