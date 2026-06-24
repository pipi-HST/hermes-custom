# Editable Capture Workbench Plan

> Date: 2026-06-19
>
> Status: implemented in `hermes-custom` and mirrored into the runtime dashboard at `/Users/pipi/Downloads/hermes-agent-main`.
>
> Goal: add a Feishu-like free editing entry point to Hermes Knowledge while preserving the existing Capture -> Library Agent -> RAG pipeline.

## Implementation Status

Updated: 2026-06-19

- Implemented editable Capture draft creation and update:
  - `CaptureAgent.create_editable_draft(...)`
  - `CaptureAgent.update_editable_draft(...)`
  - `bridge.capture_bridge.create_editable_capture_draft(...)`
  - `bridge.capture_bridge.update_editable_capture_draft(...)`
  - `POST /api/capture-drafts/editable`
  - `PATCH /api/capture-drafts/{draft_id}/editable`
- Preserved existing commit path:
  - editable drafts still commit through `CaptureAgent.commit_draft(...)`.
  - committed Capture Note body uses user-edited `draft_text`.
  - Raw Evidence preserves original `raw_text`.
  - `entry_mode` and `title_source` are stored in `capture_intent` and committed Capture Note frontmatter.
- Preserved short/long RAG indexing:
  - short editable notes create `identity` index rows.
  - long editable notes reuse `LibraryAgent.maintain_capture(...)` and create `section` index rows.
- Implemented post-commit Capture editing:
  - `PATCH /api/captures/{capture_id}`
  - backs up existing Capture Note through `KnowledgeWriteService`.
  - updates Capture Note body and optional user title.
  - re-runs targeted `LibraryAgent.maintain_capture(...)`.
  - does not mutate Raw Evidence.
- Implemented minimal workbench UI in the local dashboard:
  - `/knowledge-chat` now includes a `粘贴整理` tab.
  - users can paste/edit content, save and organize, view generated title/summary/tags, and confirm commit.
  - API client includes `createEditableCaptureDraft`, `updateEditableCaptureDraft`, and `updateCapture`.
- Mirrored the runtime dashboard:
  - `POST /api/knowledge/capture-drafts/editable`
  - `PATCH /api/knowledge/capture-drafts/{draft_id}/editable`
  - `PATCH /api/knowledge/captures/{capture_id}`
  - runtime `/knowledge-chat` exposes `粘贴整理`.
  - committed Capture materials can be edited through the review panel; saves create a backup and re-run Library maintenance.
- Verification:
  - `.venv/bin/python -m pytest tests/test_capture_draft_workflow.py tests/test_api_routes.py -q` -> `76 passed`
  - `.venv/bin/python -m pytest tests/test_capture_draft_workflow.py tests/test_capture_indexer.py tests/test_library_agent.py tests/test_rag_bridge.py tests/test_rag_graph.py tests/test_api_routes.py -q` -> `112 passed`
  - `.venv/bin/python -m pytest tests/test_api_routes.py tests/test_api_main.py tests/test_capture_draft_workflow.py tests/test_library_agent.py tests/test_capture_indexer.py -q` -> `104 passed`
  - `.venv/bin/python -m pytest tests/ -q` -> `608 passed, 2 skipped`
  - `cd dashboard && npx tsc --noEmit` -> passed
  - `cd dashboard && npx next build` -> passed
  - runtime `/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest tests/hermes_cli/test_web_server.py -k "editable_capture or capture_update" -q` -> `3 passed`
  - runtime `cd /Users/pipi/Downloads/hermes-agent-main/web && npm run build` -> passed
  - runtime dashboard smoke:
    - `GET /knowledge-chat` returns the built SPA.
    - editable draft create/delete works through `/api/knowledge/capture-drafts/editable`.
    - `PATCH /api/knowledge/captures/{capture_id}` route is loaded and authenticated.
    - Playwright snapshot shows `粘贴整理`, `保存并整理`, `确认入库`, and the free editor.

## 0. Executive Summary

Hermes should not replace the existing knowledge-ingestion pipeline. The next product step is to add a more flexible, visual, editable input surface before the current pipeline.

The user experience should become:

```text
Paste or write freely
-> Edit like a normal note
-> Save
-> Hermes generates title/tags/summary automatically
-> Existing Capture commit writes Raw Evidence and Capture Note
-> Existing Library Agent maintains outline and embeddings
-> Existing RAG retrieves via current short/long-text rules
```

The user should not manually create titles, labels, indexes, or RAG metadata. The system should do those jobs. The user only needs enough freedom to paste, edit, delete, reorder, and refine the note before and after saving.

The core product promise:

```text
You can record as freely as Feishu, but Hermes still does the Library Agent and RAG work for you.
```

## 1. Capability

After this ships, a user can paste or write arbitrary material in a free editor, edit it directly, and save it into Hermes Knowledge without manually filling title, tags, topics, or indexes. Hermes automatically generates the title and metadata, commits the material through the existing Capture pipeline, runs Library Agent post-processing, and makes the note retrievable through the existing RAG rules.

This capability is not a second knowledge system. It is a new user-facing entry surface over the existing Capture/Library/RAG workflow.

## 2. Product Positioning

### 2.1 Updated Product Frame

Hermes Knowledge should be:

```text
AI-native personal knowledge workbench:
front stage = free editing and visible control;
back stage = Library Agent organization, indexing, retrieval, and reuse.
```

### 2.2 What We Learned From the Product Discussion

- The user does not want to bypass the existing Pipeline.
- The user wants Feishu-like freedom at the input/editing layer.
- The user does not want to manually write note titles.
- The user does not want to manually manage tags, topics, indexes, or embedding behavior.
- The user wants saved notes to enter the current knowledge database and be retrievable.
- Short/long text RAG behavior must reuse the existing rules.
- The system should hide engineering complexity from the user.

### 2.3 Product Rule

User-facing complexity should stay low:

```text
User does: paste, edit, save, optionally revise later.
AI/system does: title, summary, tags, topics, outline, sections, embeddings, RAG indexing.
```

## 3. Existing System Facts To Preserve

These are current repo facts that the plan must not break.

### 3.1 Existing Capture Commit Flow

Current flow lives primarily in:

- `agents/capture_agent.py`
- `bridge/capture_bridge.py`
- `db/repo.py`
- `bridge/knowledge_write_service.py`

Current commit behavior:

```text
capture_drafts row
-> commit_draft
-> create captures row
-> write _archive/raw Markdown from raw_text
-> write captures Markdown from draft_text or raw_text
-> update captures.raw_path and captures.file_path
-> build identity embedding immediately when possible
-> run LibraryAgent.maintain_capture(...)
-> mark draft committed
```

This is the correct backbone. The editable workbench should feed into it, not bypass it.

### 3.2 Existing Durable Write Boundary

Markdown writes are centralized through:

- `bridge/knowledge_write_service.py`

The editable entry point must use the existing write path indirectly through Capture commit or through a similarly auditable write service call. It should not write vault files directly from the frontend or from ad hoc code.

### 3.3 Existing Short/Long Text Rule

The current Library Agent threshold is:

```python
DEFAULT_OUTLINE_CHAR_THRESHOLD = 2000
```

Current behavior:

- Short Capture Note: no forced outline; indexed mainly as an `identity` row.
- Long Capture Note: Library Agent generates/maintains outline and named sections; indexed as `identity` plus `section` rows.

Relevant files:

- `agents/library_agent.py`
- `agents/capture_indexer.py`
- `db/embedding_repo.py`
- `tests/test_library_agent.py`
- `tests/test_capture_indexer.py`

The new editable entry point must reuse this behavior exactly. Do not introduce a separate short/long policy in the UI or API.

### 3.4 Existing RAG Retrieval Rule

Graph-aware RAG already treats concrete Capture Note / Capture Section rows as final evidence. It should continue to do so.

Relevant files:

- `bridge/rag_bridge.py`
- `agents/rag_graph.py`
- `agents/rag_context.py`
- `agents/rag_agent.py`
- `tests/test_rag_graph.py`
- `tests/test_rag_bridge.py`
- `tests/test_rag_context.py`

Existing retrieval shape:

```text
seed retrieval
-> graph expansion
-> final evidence selection
-> answer with citations
```

The editable workbench should make new notes eligible for this same retrieval path by ensuring Capture Note files and `capture_embeddings` rows are correctly maintained.

## 4. Non-Goals

Do not do these in this project phase:

- Do not replace Capture Agent.
- Do not create a second note database.
- Do not create a parallel RAG indexing strategy.
- Do not expose identity/section/chunk/embedding decisions to the user.
- Do not require users to choose a theme before saving.
- Do not require users to write a title.
- Do not make AI suggestions a blocking modal before saving.
- Do not make Theme/Garden/Canvas part of the first editable-note MVP.
- Do not rewrite the deprecated local dashboard into a full new product shell unless the runtime dashboard cannot support the feature.

## 5. Target User Experience

### 5.1 Main Entry

Add a visible entry in the Knowledge Workbench:

```text
New note / Paste and organize
```

User action:

```text
1. Paste content or write freely.
2. Edit the text.
3. Click Save and organize.
```

User should not need to fill:

- title;
- tags;
- topic;
- summary;
- source map;
- index mode.

### 5.2 Save Feedback

The UI should show a simple status sequence:

```text
Saved draft
Generating title
Generating summary and tags
Writing to knowledge base
Building retrieval index
Ready for knowledge search
```

These labels are product language. Internally they map to existing draft, commit, Library maintenance, and embedding jobs.

### 5.3 Title Behavior

Title is AI-generated by default.

Rules:

- If the user does not edit the title, `title_source = "ai"`.
- If the user edits the title, `title_source = "user"`.
- Pipeline may update AI-generated titles.
- Pipeline must not overwrite a user-edited title.
- If a user-edited title exists, Pipeline may produce a suggestion but not apply it automatically.

For MVP, `title_source` can live inside `capture_intent` JSON or Markdown frontmatter. A schema migration is not required unless implementation finds a strong need for filtering by title source.

### 5.4 Editing Behavior

Two edit moments matter:

1. Pre-commit editing:
   - user edits draft content before it enters the durable vault;
   - safe because the draft has not become a Capture Note yet.

2. Post-commit editing:
   - user edits a committed Capture Note;
   - must create backup through the existing write-service backup pattern;
   - must re-run Library maintenance and embedding index for that capture.

MVP should prioritize pre-commit editing. Post-commit editing is P1 unless the first UI design makes it trivial.

## 6. Data and State Contract

### 6.1 Minimal MVP Data Contract

Use existing `capture_drafts` fields where possible:

```text
raw_text: original pasted material or original input
draft_text: user-editable body and source of truth for Capture Note
title: AI-generated title
summary: AI-generated summary
tags: AI-generated tags
source: optional source URL or user-entered source
source_type: note / webpage / xiaohongshu / wechat / manual
capture_intent: JSON metadata for editable entry mode
status: existing draft lifecycle state
```

Recommended `capture_intent` additions:

```json
{
  "entry_mode": "editable_capture",
  "title_source": "ai",
  "editable_source_of_truth": "draft_text",
  "pipeline_policy": "reuse_capture_library_rag"
}
```

Why this is preferred for MVP:

- avoids schema migration;
- keeps compatibility with existing `CaptureDraftRepo`;
- keeps commit behavior mostly unchanged;
- lets tests assert behavior through existing APIs.

### 6.2 When To Add Columns

Only add columns if MVP implementation shows that JSON metadata is insufficient.

Possible future columns:

```text
capture_drafts.entry_mode
capture_drafts.title_source
capture_drafts.pipeline_status
capture_drafts.last_pipeline_error
```

Do not add these speculatively. Start with JSON metadata and write-job state.

### 6.3 Committed Capture Contract

After commit, the Capture Note frontmatter should contain enough readable metadata for Library/RAG and UI:

```yaml
id: ...
title: AI generated or user edited title
date: ...
created: ...
source: ...
source_type: ...
tags: [...]
story: AI generated summary
word_count: ...
title_source: ai | user   # optional MVP field
entry_mode: editable_capture
```

The body should be the final user-edited content, not an AI rewrite unless the user intentionally accepted that rewrite into the editor.

## 7. API Contract

### 7.1 New Editable Draft Endpoint

Add:

```text
POST /api/capture-drafts/editable
```

Request:

```json
{
  "content": "pasted or typed text",
  "source": "optional URL or source label",
  "source_type": "manual",
  "instruction": "optional organizing instruction"
}
```

Behavior:

1. Create a Capture Draft.
2. Store original content in `raw_text`.
3. Store editable content in `draft_text`.
4. Generate title automatically.
5. Generate summary and tags automatically when possible.
6. Set `capture_intent.entry_mode = "editable_capture"`.
7. Return draft id, generated title, summary, tags, word count, and status.

Implementation options:

- Preferred: add `CaptureAgent.create_editable_draft(...)` so the logic is testable below the API layer.
- Acceptable MVP: add a bridge wrapper around `CaptureDraftRepo.create(...)` plus existing summarizer/title helpers, if it preserves commit behavior.

### 7.2 Editable Draft Update Endpoint

Add:

```text
PATCH /api/capture-drafts/{draft_id}/editable
```

Request:

```json
{
  "content": "new edited body",
  "title": "optional user-edited title"
}
```

Behavior:

- Update `draft_text`.
- Update `word_count`.
- If title is provided by the user, set `title_source = "user"`.
- If title is not provided and current `title_source = "ai"`, title may be regenerated.
- Regenerate summary and tags after content changes.
- Keep status commit-eligible unless the draft is incomplete or abandoned.

This endpoint should not commit. It only updates the editable draft.

### 7.3 Commit Endpoint

Reuse:

```text
POST /api/capture-drafts/{draft_id}/commit
```

The existing commit path should remain the only normal route into durable Capture Note creation.

Acceptance condition:

- Editable draft commits through `CaptureAgent.commit_draft(...)`.
- Commit writes Raw Evidence and Capture Note.
- Commit runs `_index_committed_identity(...)`.
- Commit runs `_postprocess_committed_library(...)`.
- `KnowledgeWriteJobRepo` records pending/failed embedding state as it does today.

### 7.4 Post-Commit Edit Endpoint

P1, not required for MVP:

```text
PATCH /api/captures/{capture_id}
```

Request:

```json
{
  "body": "updated note body",
  "title": "optional user title"
}
```

Behavior:

1. Load capture by id.
2. Read current Markdown.
3. Backup existing capture file through `KnowledgeWriteService` with `requires_backup=True`.
4. Write updated Markdown.
5. Update frontmatter word count and title metadata.
6. Run `LibraryAgent.maintain_capture(capture_id, apply=True)`.
7. Return index status and warnings.

This must not directly mutate raw evidence unless the user explicitly asks to edit source evidence.

## 8. Backend Implementation Plan

### Phase 1: Editable Draft Creation

Files likely touched:

- `agents/capture_agent.py`
- `bridge/capture_bridge.py`
- `api/routes/capture_drafts.py`
- `db/repo.py` only if current allowed update fields are insufficient
- `tests/test_capture_draft_workflow.py`
- `tests/test_api_routes.py`

Implementation steps:

1. Add a focused test for creating an editable draft.
2. Implement `CaptureAgent.create_editable_draft(...)`.
3. Add `capture_bridge.create_editable_capture_draft(...)`.
4. Add `POST /api/capture-drafts/editable`.
5. Ensure returned envelope or API response includes draft id, title, summary, tags, and status.
6. Ensure no vault write happens before commit.

Verification:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py -q
.venv/bin/python -m pytest tests/test_api_routes.py -q
```

### Phase 2: Editable Draft Update

Files likely touched:

- `agents/capture_agent.py`
- `bridge/capture_bridge.py`
- `api/routes/capture_drafts.py`
- `dashboard/src/lib/api.ts`
- `tests/test_capture_draft_workflow.py`
- `tests/test_api_routes.py`

Implementation steps:

1. Add tests for updating `draft_text`.
2. Add tests for title-source behavior:
   - AI title can be regenerated before user edits it.
   - user-edited title is not overwritten.
3. Implement update method.
4. Regenerate summary/tags after body update.
5. Preserve draft status unless invalid transition.

Verification:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py tests/test_api_routes.py -q
```

### Phase 3: Commit and Library/RAG Reuse

Files likely touched:

- Prefer no changes to `CaptureAgent.commit_draft(...)`.
- Add regression tests in `tests/test_capture_draft_workflow.py`.
- Add focused Library tests if needed in `tests/test_library_agent.py`.
- Add RAG/index tests if needed in `tests/test_capture_indexer.py`.

Implementation steps:

1. Commit an editable draft in a test.
2. Assert raw evidence file is written from `raw_text`.
3. Assert capture note file is written from user-edited `draft_text`.
4. Assert title, summary, tags are present in Capture Note frontmatter.
5. Assert Library postprocess is invoked.
6. Assert short note creates identity index and no unnecessary section index.
7. Assert long note creates identity plus section indexes through existing rules.

Important: do not add a new short/long threshold. Use `LibraryAgent` and `capture_indexer`.

Verification:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py tests/test_library_agent.py tests/test_capture_indexer.py -q
```

### Phase 4: Workbench UI

Files likely touched:

- `dashboard/src/pages/knowledge-chat.tsx`
- `dashboard/src/pages/drafts.tsx` or a new page if preferred
- `dashboard/src/components/DraftReviewPanel.tsx`
- `dashboard/src/lib/api.ts`
- `dashboard/src/styles/globals.css`

UI requirements:

- Add an editor surface for new editable capture.
- Provide a large free-text textarea/editor.
- Do not require title input.
- Show generated title after save.
- Let the user edit title only as an optional override.
- Show generated tags and summary as metadata, not required fields.
- Show status labels for saved, organizing, committed, indexed.
- Keep Capture Review available for existing draft flow.

Recommended first UI shape:

```text
Left: drafts / recent captures
Center: free editor
Right: generated metadata and pipeline status
```

Do not overbuild a full Feishu clone. The first UX goal is fast paste/edit/save plus visible pipeline status.

Verification:

```bash
cd dashboard
npm run build
```

If runtime dashboard lives in `/Users/pipi/Downloads/hermes-agent-main`, mirror only the needed route/tool surface there after the `hermes-custom` API contract is stable.

### Phase 5: Post-Commit Editing and Reindexing

P1 after MVP. Do not block Phase 1-4 on this.

Files likely touched:

- `api/routes/captures.py`
- `bridge/knowledge_write_service.py` if backup ergonomics need a helper
- `agents/library_agent.py` only if targeted reindex has a gap
- `dashboard/src/lib/api.ts`
- capture detail/editor UI
- `tests/test_api_routes.py`
- `tests/test_library_agent.py`
- new tests for committed note editing

Implementation steps:

1. Add a capture update API.
2. Backup before overwrite.
3. Update Markdown body and optional title.
4. Re-run `LibraryAgent.maintain_capture(...)`.
5. Verify `capture_embeddings` source hashes update after edit.
6. Show UI status: "re-indexing" and "ready".

Verification:

```bash
.venv/bin/python -m pytest tests/test_api_routes.py tests/test_library_agent.py tests/test_capture_indexer.py -q
```

## 9. Frontend Product Details

### 9.1 MVP Editor States

The editor should have these states:

```text
empty
editing
saving_draft
draft_saved
organizing
ready_to_commit
committing
committed
index_warning
error
```

Do not expose internal exception traces in the UI. Show a concise failure and keep the draft body editable.

### 9.2 Required UI Copy

Use simple user-facing labels:

```text
Paste and organize
Save and organize
Generated title
Summary
Tags
Ready for knowledge search
Needs index repair
```

Avoid:

```text
identity embedding
section embedding
capture_embeddings
LibraryAgent
Graph-aware RAG
```

### 9.3 Metadata Panel

Metadata should be visible but not mandatory:

```text
Title: generated by Hermes
Summary: generated by Hermes
Tags: generated by Hermes
Source: optional
Status: ready / indexing / warning
```

The user can edit the title, but should not be forced to.

## 10. RAG and Indexing Contract

### 10.1 Short Notes

For notes shorter than the existing Library threshold:

```text
Capture Note body < DEFAULT_OUTLINE_CHAR_THRESHOLD
-> no forced outline
-> identity index
-> retrievable as whole note
```

Expected tests:

- short editable draft commits successfully;
- `capture_embeddings` contains `kind = "identity"`;
- no forced sections are created unless user provided real headings.

### 10.2 Long Notes

For longer notes:

```text
Capture Note body >= DEFAULT_OUTLINE_CHAR_THRESHOLD
-> Library Agent generates/uses outline
-> Capture Sections become named addressable retrieval units
-> section indexes are created
-> RAG can cite Capture Section
```

Expected tests:

- long editable draft commits successfully;
- outline metadata exists;
- `capture_embeddings` contains `identity` and `section` rows;
- RAG final evidence can refer to section title.

### 10.3 Search Eligibility

Every committed editable note should become RAG-eligible through the existing commit path.

If embedding fails:

- note must still be saved;
- write job should mark repair needed;
- UI should show index warning;
- a later repair/reindex should be possible.

Do not block saving on embedding failure.

## 11. Observability and Failure Handling

### 11.1 Write Jobs

Continue using `KnowledgeWriteJobRepo` for commit visibility.

Editable capture should surface:

```text
draft saved
file write complete
identity embedding present or repair needed
Library postprocess warnings
```

### 11.2 Failure Rules

- If title generation fails, save draft with a fallback title and warning.
- If summary/tag generation fails, save draft and allow retry.
- If commit fails before file write, draft remains open.
- If file write succeeds but embedding fails, capture remains committed and repair is marked.
- If Library outline fails, capture remains committed and repair warning is shown.
- If frontend save fails, keep user text in the editor.

### 11.3 Logging

Do not add noisy logs. Prefer existing ledger/write job records. Add logs only for:

- title generation failure;
- commit failure;
- Library postprocess failure;
- embedding failure.

## 12. Test Plan

### 12.1 Unit Tests

Add or update:

- `tests/test_capture_draft_workflow.py`
  - editable draft create;
  - editable draft update;
  - AI title default;
  - user title override;
  - commit writes edited body;
  - commit writes raw evidence.

- `tests/test_capture_indexer.py`
  - short editable note identity index;
  - long editable note section index;
  - generated title participates in identity index text.

- `tests/test_library_agent.py`
  - short editable note skips outline when below threshold;
  - long editable note gets outline/sections through existing threshold.

- `tests/test_api_routes.py`
  - editable create endpoint;
  - editable update endpoint;
  - commit endpoint still works.

### 12.2 Integration Tests

Add or update:

- `tests/test_e2e_full_flow.py`
  - paste/edit/save/commit/search path.

- `tests/test_knowledge_workbench_smoke.py`
  - UI route exposes editable capture entry if dashboard in this repo is still tested.

### 12.3 RAG Verification

Add a focused RAG fixture if practical:

```text
Create short editable note -> commit -> query exact concept -> result cites Capture Note.
Create long editable note -> commit -> query section-specific concept -> result cites Capture Section.
```

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py tests/test_capture_indexer.py tests/test_library_agent.py tests/test_rag_bridge.py tests/test_rag_graph.py -q
.venv/bin/python -m pytest tests/ -q
```

### 12.4 Frontend Build

Run:

```bash
cd dashboard
npm run build
```

If changes are mirrored into Hermes runtime:

```bash
cd /Users/pipi/Downloads/hermes-agent-main/web
npm run build
```

## 13. Rollout Plan

### Step 1: Backend MVP Only

Ship API and tests first. No broad UI change.

Acceptance:

- editable draft can be created via API;
- commit works;
- short/long indexing behavior is preserved.

### Step 2: Minimal UI

Add a simple editor to the knowledge workbench.

Acceptance:

- paste text;
- save and see generated title;
- commit and see status.

### Step 3: Status and Repair Visibility

Expose index status and Library warnings.

Acceptance:

- successful index shows ready;
- embedding failure shows warning without losing note.

### Step 4: Runtime Alignment

If the actual user-facing surface is Hermes runtime, port the stable API usage and UI route there.

Acceptance:

- runtime route loads;
- gateway/dashboard routes still return 200;
- capture/review/cards existing routes are not broken.

### Step 5: Post-Commit Editing

Add committed note editing after MVP proves the entry flow.

Acceptance:

- editing a committed capture creates backup;
- reindex updates source hashes;
- RAG can find updated content.

## 14. Dependency and Ordering Risks

### 14.1 Risk: Breaking Existing Capture Flow

Mitigation:

- add new editable methods instead of changing `create_draft` semantics broadly;
- preserve `commit_draft`;
- run full capture tests.

### 14.2 Risk: Title Handling Becomes Confusing

Mitigation:

- default title source is AI;
- user override is explicit;
- never overwrite a user title silently.

### 14.3 Risk: New Notes Save But Do Not Search

Mitigation:

- assert `capture_embeddings` rows after commit;
- inspect write job repair status;
- add RAG smoke tests.

### 14.4 Risk: Long Notes Lose Section-Level Retrieval

Mitigation:

- do not bypass `LibraryAgent.maintain_capture`;
- do not lower or duplicate `DEFAULT_OUTLINE_CHAR_THRESHOLD`;
- test long note with headings and without headings.

### 14.5 Risk: UI Looks Free But Backend Still Blocks On LLM

Mitigation:

- MVP may be synchronous, but UI must keep editor text safe during pending state;
- P1 should move title/tag generation into a visible background status if latency is bad;
- saving draft should never discard user text.

### 14.6 Risk: Runtime Repo Divergence

Mitigation:

- stabilize `hermes-custom` API contract first;
- only then update `/Users/pipi/Downloads/hermes-agent-main`;
- keep route names and response shapes documented.

## 15. Acceptance Criteria

The feature is done only when all are true:

- User can paste text into an editor and save without writing a title.
- Hermes generates a title automatically.
- User can edit content before commit.
- Committed note uses the user-edited content as the Capture Note body.
- Raw Evidence preserves the original source text where available.
- Commit uses the existing durable write path.
- Commit runs existing Library Agent postprocess.
- Short note is retrievable through identity index.
- Long note is retrievable through section-level index.
- RAG uses existing graph-aware retrieval path.
- UI shows simple status without exposing implementation jargon.
- Existing capture/reading/RAG tests still pass.

## 16. Recommended First Implementation Slice

The smallest useful slice:

```text
POST /api/capture-drafts/editable
PATCH /api/capture-drafts/{draft_id}/editable
existing POST /api/capture-drafts/{draft_id}/commit
minimal editor UI
tests proving short/long indexing
```

This slice directly addresses the user's pain:

- free paste/edit like Feishu;
- AI-generated title;
- no manual tags/indexes;
- existing Pipeline still handles knowledge ingestion;
- RAG can retrieve the result.

## 17. Open Questions

These should be answered during implementation, not guessed too early:

1. Should AI title generation happen synchronously on draft save, or should the draft save immediately and title appear after background processing?
   - MVP can be synchronous if latency is acceptable.
   - If latency is poor, split into immediate draft save plus background metadata job.

2. Should the first UI live in the local `dashboard/` or the Hermes runtime dashboard?
   - Implement and test API in `hermes-custom` first.
   - Mirror UI to runtime only after API stabilizes.

3. Should post-commit editing ship in MVP?
   - Recommended: no.
   - Build pre-commit editable flow first, then add committed editing with backups and reindex.

4. Should user-edited title live in `capture_intent`, Markdown frontmatter, or a new DB column?
   - Recommended MVP: `capture_intent` plus optional frontmatter.
   - Add DB column only when filtering/reporting requires it.

## 18. Handoff

This plan is ready for implementation after one architecture sanity check:

- confirm the editable draft methods can reuse `CaptureDraftRepo` without migration;
- confirm title generation can reuse existing title/intent/summarizer helpers;
- confirm runtime UI location before frontend work begins.

Suggested execution lane:

```text
Backend first:
  CaptureAgent + bridge + API + tests

Then frontend:
  minimal editor + status panel

Then verification:
  short note RAG
  long note RAG
  full capture regression
```
