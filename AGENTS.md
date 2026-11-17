---
status: current
owner: knowledge-service
last_verified: 2026-11-17
verified_against: "e4cfdda (hermes-custom) / ceac73d (hermes-agent-main) + 2026-11-17 working tree"
---

# AGENTS.md — Hermes Knowledge

These rules apply to AI agents and developers working in this repository. Read them before editing code.

## 1. Read first

1. `README.md` — what this project is and how to start it.
2. `ARCHITECTURE.md` — ownership, data truth, write paths.
3. `GLOSSARY.md` — the only allowed user-facing terms.
4. `RUNBOOK.md` — start/stop/troubleshoot/reset.
5. `CONTEXT.md` — domain language and product workflow.

If a task conflicts with these, stop and ask. Do not silently pick one interpretation.

## 2. Architecture rules

1. **Knowledge Service owns Knowledge writes.** Runtime Dashboard must call the HTTP service in production. Do not add direct SQL or vault writes to `hermes-agent-main` for Knowledge.
2. **One writer per object.** Capture → `capture_bridge`; Card → `garden_bridge`; Reading → `reading_bridge`; Topic → `rename_topic`; Review → `review_agent`.
3. **Markdown is the Capture truth.** Do not mirror title/summary/tags into SQLite. `capture_metadata` is an index; it must be repairable from Markdown.
4. **Cards/Themes/Review are SQLite truth.** Markdown exports are optional and disposable.
5. **No new table or column without:** migration in `db/schema.py`, a reader/writer, an invariant test, and a doc update.
6. **No new user-facing term without** adding it to `GLOSSARY.md` and removing the old term from current docs.
7. **No big rename.** Do not rename database tables, directories, or public bridge functions just to match product copy. Add a compatibility layer and a migration plan first.

## 3. Definition of Done

A change is not done until all of these are true:

- [ ] Behavior is covered by a test that fails before and passes after.
- [ ] Full `pytest tests` is green (or the exact unrelated failures are recorded).
- [ ] `python scripts/http_only_smoke.py` still passes if the change touches Runtime ↔ Service transport.
- [ ] `docs/CURRENT-STATE.md` or any hand-written count is updated if it changed.
- [ ] Current docs (`README`, `ARCHITECTURE`, `GLOSSARY`, `RUNBOOK`, `CONTEXT`) are consistent with the change.
- [ ] No real API key, token, or personal path is added to docs, tests, logs, or commits.
- [ ] If Runtime was touched: `python scripts/fork_delta.py --check` is clean.

## 4. Testing

```bash
cd hermes-custom
python -m pytest tests -q

# HTTP-only transport
python scripts/http_only_smoke.py

# Topic repair
python scripts/repair_topics.py --config config.yaml
```

Runtime side:

```bash
cd hermes-agent-main
python -m pytest tests/tools tests/hermes_cli/test_web_server.py -q
```

Do not use real LLM / embedding providers in tests. Use the local fake provider pattern from `scripts/knowledge_pipeline_e2e.py`.

## 5. Data invariants

- `captures/*.md` frontmatter `folder_path` is the source of truth for a Capture's topic.
- `capture_metadata.folder_path` must match frontmatter after every topic write.
- `garden_cards.source_theme` must match the card's captured topic after a topic rename.
- `capture_embeddings` is the only capture vector index; `captures.embedding` was removed in schema v4.
- `review_items` is the review scheduler; `captures.review_*` was removed in schema v4.
- `captures.archived_at` is the only archive marker; do not add a second status column.
- Deleting a Topic must not delete the underlying Capture or Card.

Add a regression test for any invariant you touch.

## 6. Documentation rules

Current docs carry a visible status line or YAML front matter:

```yaml
---
status: current        # current | historical | superseded | proposal
owner: <name/team>
last_verified: YYYY-MM-DD
verified_against: <commit>
---
```

- `docs/history/` is for old handoffs, plans, specs, code checkpoints. Do not use it as a runbook.
- `docs/checkpoints/` is archival only. Never copy old checkpoint code over the current runtime.
- Plans go in `docs/plans/active/` while active; when done, add a result section and move to `docs/history/plans/`.
- Do not hand-write test counts, table counts, or route counts. Generate them in `docs/CURRENT-STATE.md`.

## 7. Runtime repository rules

`hermes-agent-main` is a fork, not a branch of upstream. Read its `docs/fork-upstream.md`.

- Prefer `plugins/`, `integrations/`, `gateway/builtin_hooks/` over patching upstream core.
- `web_server.py`, `conversation_loop.py`, `tool_executor.py`, `gateway/run.py`, `model_tools.py` are frozen for new custom logic unless there is no documented seam.
- Every patched upstream file must appear in the generated fork table; run `python scripts/fork_delta.py --write` after the change.
- Every new tool that calls a bridge must use `tools.hermes_custom_envelope.call_custom_bridge`, not a direct `bridge.*` import.

## 8. When unsure

- If the request has multiple plausible meanings, ask.
- If a simpler approach exists, say so.
- If a change would create a second write path, stop and propose the single-writer route.
- If a doc and the code disagree, trust the code for behavior and the doc for intent; record the mismatch, don't silently rewrite history.
