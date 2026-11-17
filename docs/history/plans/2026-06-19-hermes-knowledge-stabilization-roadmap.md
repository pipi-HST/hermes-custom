---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Hermes Knowledge Stabilization Roadmap

> **For agentic workers:** implement this plan task-by-task. Do not start broad refactors. Each phase must preserve current behavior unless the phase explicitly changes the contract. Run the listed verification before moving to the next phase.

**Goal:** Stabilize Hermes Knowledge after the June 2026 product/architecture audit. The work should turn the current capability-rich knowledge system into a more reproducible, safer, and easier-to-evolve AI product without adding speculative features.

**Scope:** `/Users/pipi/hermes-custom` first. Touch `/Users/pipi/Downloads/hermes-agent-main` only when a phase explicitly requires runtime dashboard/tool registry alignment.

**Current product shape:** `hermes-custom` owns the Knowledge capability layer: Capture, Reading, RAG, Theme, Garden/Knowledge Cards, Canvas bridge, SQLite state, Obsidian Markdown artifacts, and FastAPI routes. Hermes runtime owns the user-facing gateway/dashboard.

**Primary success criteria:**

- A new operator can start, test, and understand the project from current docs.
- Knowledge writes have one auditable path for Markdown, SQLite state, backups, write jobs, and ledger records.
- RAG answers either preserve valid citations or clearly degrade with warnings.
- Extractor failures are structured failures, not accidental knowledge content.
- User-facing language describes a product workflow, not internal agent names.

## Execution Status

Updated: 2026-06-19

- Phase 0 complete: baseline recorded in `docs/superpowers/plans/2026-06-19-hermes-stabilization-baseline.md`.
- Phase 1 complete: `SETUP.md`, `HANDOFF.md`, `config.example.yaml`, and `requirements.txt` now reflect current runtime/dashboard/dependency expectations.
- Phase 2 complete: `CONTEXT.md`, `CONTEXT-MAP.md`, and `docs/superpowers/plans/2026-06-19-hermes-product-language-audit.md` now define the product workflow and user-facing vocabulary.
- Phase 3 complete: `bridge/knowledge_write_service.py` now owns Markdown artifact writes for Garden commit, Reading independent-note / knowledge-card commit, Answer-to-Reading commit, Capture commit, and enhanced-capture overwrite paths. Enhanced Capture backups now use vault-local `_archive/write_backups/...` paths.
- Phase 4 complete: sourced RAG answers now run citation validation, attempt one citation repair pass when validation fails, then re-validate and warn only if the repaired answer is still invalid.
- Phase 5 complete: extractors now support typed `ExtractionFailure`, and Capture stops typed extraction failures before creating drafts so failure placeholder text is not saved as knowledge content.
- Phase 6 complete: `bridge/context.py` provides `KnowledgeContext`; RAG, Reading, Garden, Theme, and Capture bridges accept optional explicit context while preserving default runtime initialization.
- Phase 7 complete: Hermes runtime now exposes `/knowledge-chat`, `/capture-review`, and `/cards-board` as Knowledge Workbench/Card surfaces; normal personal-assistant tools keep Capture, Reading, RAG, Answer Draft, and Knowledge Card flows visible while Theme/Canvas remain in `knowledge-advanced`.
- Verification after Phase 4 warning-only validator:
  - `.venv/bin/python -m pytest tests/test_api_main.py tests/test_api_routes.py -q` -> `17 passed`
  - `.venv/bin/python -m pytest tests/test_rag_citations.py -q` -> `4 passed`
  - `.venv/bin/python -m pytest tests/test_rag_bridge.py tests/test_rag_file_output.py -q` -> `7 passed`
  - `.venv/bin/python -m pytest tests/ -q` -> `581 passed, 2 skipped`
- Verification after Phase 3 write-service Garden/Reading slice:
  - `.venv/bin/python -m pytest tests/test_knowledge_write_service.py -q` -> `2 passed`
  - `.venv/bin/python -m pytest tests/test_garden_bridge.py tests/test_garden_card_payload.py -q` -> `20 passed`
  - `.venv/bin/python -m pytest tests/test_reading_bridge.py -q` -> `12 passed`
  - `.venv/bin/python -m pytest tests/ -q` -> `583 passed, 2 skipped`
- Verification after Phase 4 citation repair loop:
  - `.venv/bin/python -m pytest tests/test_rag_citations.py tests/test_rag_bridge.py tests/test_rag_file_output.py -q` -> `12 passed`
  - `.venv/bin/python -m pytest tests/ -q` -> `584 passed, 2 skipped`
- Verification after Phase 5 typed extractor failures:
  - `.venv/bin/python -m pytest tests/test_extraction_failures.py tests/test_media_capture.py tests/test_xiaohongshu_extractor.py -q` -> `38 passed`
  - `.venv/bin/python -m pytest tests/test_capture_draft_workflow.py -q` -> `52 passed`
  - `.venv/bin/python -m pytest tests/ -q` -> `589 passed, 2 skipped`
- Verification after Phase 3 completion and Phase 6 context cleanup:
  - `.venv/bin/python -m pytest tests/test_capture_draft_workflow.py::test_commit_draft_uses_shared_write_service_for_raw_and_capture_files -q` -> `1 passed`
  - `.venv/bin/python -m pytest tests/test_capture_draft_workflow.py tests/test_capture_file_output.py -q` -> `58 passed`
  - `.venv/bin/python -m pytest tests/test_reading_bridge.py::test_commit_enhanced_capture_backs_up_and_rewrites_capture -q` -> `1 passed`
  - `.venv/bin/python -m pytest tests/test_knowledge_write_service.py tests/test_garden_bridge.py tests/test_garden_card_payload.py tests/test_reading_bridge.py tests/test_answer_bridge.py tests/test_capture_draft_workflow.py tests/test_capture_file_output.py -q` -> `101 passed`
  - `.venv/bin/python -m pytest tests/test_bridge_context.py -q` -> `4 passed`
  - `.venv/bin/python -m pytest tests/test_api_main.py tests/test_api_routes.py -q` -> `17 passed`
  - `.venv/bin/python -m pytest tests/test_bridge_context.py tests/test_rag_bridge.py tests/test_reading_bridge.py tests/test_garden_bridge.py tests/test_theme_bridge.py tests/test_capture_file_output.py -q` -> `71 passed`
  - `.venv/bin/python -m pytest tests/ -q` -> `596 passed, 2 skipped`
- Verification after Phase 7 runtime alignment:
  - `/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest tests/test_toolsets.py tests/tools/test_knowledge_product_tools.py tests/tools/test_capture_tool.py tests/tools/test_search_notes_tool.py tests/tools/test_save_answer_to_knowledge_tool.py tests/tools/test_garden_draft_tools.py tests/tools/test_theme_scan_tool.py tests/tools/test_theme_candidates_tool.py tests/tools/test_generate_canvas_tool.py tests/hermes_cli/test_web_server.py::TestWebServerEndpoints::test_knowledge_capture_drafts_api_returns_reviewable_drafts tests/hermes_cli/test_web_server.py::TestWebServerEndpoints::test_knowledge_cards_api_returns_grouped_cards_with_readable_sources -q` -> `58 passed, 2 warnings`
  - `cd /Users/pipi/Downloads/hermes-agent-main/web && npm run build` -> passed
  - `.venv/bin/python -m pytest tests/test_e2e_full_flow.py tests/test_capture_draft_workflow.py tests/test_rag_bridge.py tests/test_rag_citations.py tests/test_reading_bridge.py tests/test_answer_bridge.py tests/test_extraction_failures.py -q` -> `99 passed`
  - `.venv/bin/python -m pytest tests/test_knowledge_workbench_smoke.py -q` -> `4 passed`
  - `.venv/bin/python -m pytest tests/ -q` -> `600 passed, 2 skipped`
  - Runtime restarted: `gateway restart` succeeded; dashboard restarted on `127.0.0.1:9119`.
  - Route probes after restart: `/knowledge-chat`, `/capture-review`, and `/cards-board` -> `200 text/html`.

---

## 0. Non-Negotiable Constraints

- Do not rewrite unrelated code while executing a phase.
- Do not remove existing dirty work unless explicitly requested by the user.
- Do not print, copy, or commit real API keys from `config.yaml`.
- Keep Obsidian file layout compatible with current vault usage:
  - `raw/` or `_archive/raw/` for source evidence.
  - `captures/` for processed capture notes.
  - `themes/` for relationship/theme pages.
  - `garden/` for reusable Knowledge Cards.
- Preserve the draft-before-commit product rule for state-changing AI output.
- Keep Life Assistant isolated from Knowledge; do not introduce shared domain data.
- Prefer deterministic contracts for invariants, schemas, permissions, source selection, fallback behavior, and citation validation.
- Use LLM reasoning only for ambiguous language tasks such as summarization, prioritization, synthesis, explanation, and presentation.

---

## 1. Dependency Graph

```text
Phase 0: Baseline snapshot
  -> Phase 1: Docs and dependency reproducibility
    -> Phase 2: Product vocabulary and main workflow contract
      -> Phase 4: RAG citation contract
      -> Phase 7: Product/runtime UX alignment
    -> Phase 3: Unified knowledge write service
      -> Phase 5: Typed extractor failures
      -> Phase 6: Context/repository boundary cleanup
```

Reasoning:

- Docs and dependencies come first because every later phase needs reliable startup and tests.
- Product vocabulary comes before citation/UI work because user-facing terms should not be churned repeatedly.
- Write-service consolidation comes before extractor failure handling because extraction failure handling affects whether a write is allowed.
- Runtime/UI alignment comes last because the current local `dashboard/` is deprecated and the real product surface lives outside this repo.

---

## 2. Phase 0: Baseline Snapshot

**Purpose:** Establish an execution baseline so later changes are measurable and reversible.

**Code changes:** None.

**Commands:**

```bash
cd /Users/pipi/hermes-custom
git status --short --branch
.venv/bin/python -m pytest tests/ -q
```

**Record:**

- Current branch name.
- Dirty files before work begins.
- Test result and skipped tests.
- Whether the runtime dashboard is expected at `http://127.0.0.1:9119/`.
- Whether `dashboard/` is still deprecated.

**Acceptance criteria:**

- Baseline is written into the implementation notes or PR description.
- No files changed in this phase.
- Any failing tests are classified before implementation starts:
  - current-environment failure;
  - known unrelated failure;
  - failure caused by the current phase.

**Rollback:** Not applicable.

---

## 3. Phase 1: Docs and Dependency Reproducibility

**Purpose:** Make the project startable and understandable from current documentation.

### 3.1 Files

Expected changes:

- Modify `SETUP.md`.
- Modify `HANDOFF.md`.
- Modify `config.example.yaml`.
- Add either `pyproject.toml` or `requirements.txt`.
- Optionally modify `.gitignore` only if sensitive/local files are not already ignored.

Do not modify business logic in this phase.

### 3.2 Required documentation updates

`SETUP.md` must state:

- `hermes-custom` is the Knowledge capability repo.
- FastAPI runs on `127.0.0.1:8000`.
- The old local Next dashboard under `dashboard/` is deprecated.
- The current user-facing Hermes dashboard is provided by Hermes runtime, normally on `127.0.0.1:9119`.
- Runtime/gateway lives in `/Users/pipi/Downloads/hermes-agent-main`.
- `config.yaml` is local and sensitive.

`HANDOFF.md` must state:

- Old table counts, test counts, and dashboard startup instructions may be stale.
- Current operators should verify with `db/schema.py`, `tests/`, and `dashboard/DEPRECATED.md`.
- The durable handoff still helps with historical context, but it is not the live runbook.

`config.example.yaml` must:

- Use placeholders only.
- Contain every config section needed by current imports, including LLM, Qwen/DashScope, database, and vault settings.
- Avoid realistic-looking secrets.

### 3.3 Dependency file

Prefer `pyproject.toml` if the repo is ready for it. Use `requirements.txt` if minimal change is safer.

Minimum dependency inventory to confirm:

- `fastapi`
- `uvicorn`
- `pydantic`
- `PyYAML`
- `httpx`
- `pytest`
- `pypdf`
- optional web extraction libraries already used by code, such as `trafilatura` and readability-related packages

Do not guess pinned versions unless local environment evidence supports them. If exact versions are unknown, use conservative unpinned or lower-bound dependencies and document that this is a bootstrap list.

### 3.4 Verification

```bash
cd /Users/pipi/hermes-custom
.venv/bin/python -m pytest tests/test_api_main.py tests/test_api_routes.py -q
.venv/bin/python -m pytest tests/ -q
```

If a fresh install path is tested:

```bash
python -m venv /tmp/hermes-knowledge-venv
/tmp/hermes-knowledge-venv/bin/python -m pip install -r requirements.txt
```

or the equivalent `pyproject.toml` install command.

### 3.5 Risks

- Adding an incomplete dependency file can create false confidence.
- Updating `HANDOFF.md` too aggressively can erase useful historical context.
- Accidentally exposing secrets is the highest-severity risk in this phase.

### 3.6 Rollback

- Revert only Phase 1 documentation/dependency files.
- Do not touch generated vault content or SQLite data.

---

## 4. Phase 2: Product Vocabulary and Main Workflow Contract

**Purpose:** Make product language coherent before engineering contracts are tightened.

### 4.1 Files

Expected changes:

- Modify `CONTEXT.md`.
- Optionally modify `CONTEXT-MAP.md`.
- Optionally add an ADR if terminology changes become a stable product decision.

### 4.2 User-facing vocabulary

Use these as the default product terms:

| Product term | Meaning | Internal implementation may include |
| --- | --- | --- |
| Capture | A saved source material and its processed note | Capture Agent, extractor, raw archive, capture draft |
| Reading Session | A temporary discussion around one or more sources | reading sessions, reading turns, evidence, reading drafts |
| Draft | Reviewable AI output before writing durable knowledge | capture_drafts, reading_drafts, garden_drafts |
| Knowledge Card | A reusable knowledge object | Garden card, `garden_cards`, `garden/*.md` |
| Source | Citable evidence used by AI output | capture, theme, garden card, source_map |

Avoid making users understand these terms by default:

- Agent names.
- Theme scan.
- MOC.
- Canvas.
- Run ledger.
- Embedding index.

Those can remain advanced/system concepts.

### 4.3 Main workflow

Document the product workflow as:

```text
material or question
  -> AI reads/discusses with sources
  -> draft is produced
  -> user reviews and confirms
  -> durable knowledge is written
  -> knowledge can be cited and reused
```

State explicitly:

- AI may prepare a draft without confirmation.
- AI must not perform durable writes without a confirmable action.
- Long outputs should be available as files or full views, not only chat previews.
- Source traceability is part of the product contract, not an optional debug detail.

### 4.4 Verification

Search for old product-language conflicts:

```bash
cd /Users/pipi/hermes-custom
rg "Garden|知识花园|Theme|MOC|Canvas|Capture Review|讨论札记" CONTEXT.md CONTEXT-MAP.md docs -n
```

The goal is not to remove every occurrence. The goal is to classify them:

- acceptable internal/legacy reference;
- user-facing wording that should be changed;
- stale documentation.

### 4.5 Risks

- Renaming database concepts prematurely can create unnecessary migration work.
- Removing internal concepts from docs can make engineering harder.

### 4.6 Rollback

- Revert documentation changes only.
- Do not rename code identifiers in this phase.

---

## 5. Phase 3: Unified Knowledge Write Service

**Purpose:** Give durable knowledge writes one auditable path.

### 5.1 Current problem

The current bridge layer contains repeated write orchestration:

- write Markdown;
- write or update SQLite rows;
- create backups;
- record write jobs;
- record ledger events;
- shape preview messages;
- return result envelopes.

This repetition increases risk when write behavior changes.

### 5.2 Proposed module

Add:

```text
bridge/knowledge_write_service.py
tests/test_knowledge_write_service.py
```

Initial service responsibilities:

- Accept a typed write request.
- Write or update the target Markdown artifact.
- Update the associated repository row.
- Record backup metadata when an existing artifact is modified.
- Record `knowledge_write_jobs` where applicable.
- Return a structured write result suitable for `ResultEnvelope`.

Out of scope for the first version:

- Rewriting every bridge.
- Changing Obsidian file naming rules.
- Changing user-facing preview content.
- Migrating existing data.

### 5.3 Suggested request types

```python
@dataclass
class KnowledgeWriteRequest:
    kind: Literal[
        "capture_note",
        "knowledge_card",
        "reading_note",
        "answer_note",
        "theme_page",
    ]
    title: str
    body: str
    frontmatter: dict[str, Any]
    sources: list[dict[str, Any]]
    draft_id: str | None = None
    existing_path: str | None = None
    requires_backup: bool = False
```

```python
@dataclass
class KnowledgeWriteResult:
    kind: str
    file_path: str
    db_object_id: str | None
    backup_path: str | None
    write_job_id: str | None
    warnings: list[str]
```

Adjust exact typing to match the codebase's current Python version and style.

### 5.4 Migration order

Use the safest-to-riskier order:

1. Garden/Knowledge Card commit.
2. Reading independent note.
3. Reading knowledge card commit.
4. Answer draft commit.
5. Capture commit.
6. Theme page update, only if it naturally fits.

Do not migrate all call sites in one patch.

### 5.5 Tests per migration step

Garden:

```bash
.venv/bin/python -m pytest tests/test_garden_bridge.py tests/test_garden_card_payload.py -q
```

Reading:

```bash
.venv/bin/python -m pytest tests/test_reading_bridge.py -q
```

Answer:

```bash
.venv/bin/python -m pytest tests/test_rag_bridge.py tests/test_rag_file_output.py -q
```

Capture:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py tests/test_capture_file_output.py -q
```

Full:

```bash
.venv/bin/python -m pytest tests/ -q
```

### 5.6 Acceptance criteria

- At least Garden and Reading writes use the new service before the phase is considered useful.
- Existing output files remain compatible with Obsidian.
- Existing tests pass.
- New tests prove backups/write jobs/ledger behavior is not lost.
- Result envelopes still expose durable artifacts and next actions.

### 5.7 Risks

- High risk of broad regressions if Capture is migrated too early.
- Write service can become another large bridge if preview shaping and product copy are mixed into it.
- If transaction boundaries are unclear, partial writes may become harder to reason about.

### 5.8 Rollback

- Because migration is incremental, rollback one call site at a time.
- Keep old bridge helper functions until all migrated paths are stable.
- Do not delete old helpers in the same patch that introduces the service.

---

## 6. Phase 4: RAG Citation Contract

**Purpose:** Make citations enforceable, not merely prompt-requested.

### 6.1 Files

Expected changes:

- Add `agents/rag_citations.py` or `bridge/rag_citations.py`.
- Modify `bridge/rag_bridge.py`.
- Modify tests in `tests/test_rag_bridge.py`.
- Add focused tests if the validator is its own module.

### 6.2 Contract

A successful sourced RAG answer should satisfy:

- Every citation marker in the final answer maps to an existing source.
- Every source displayed to the user has a stable index.
- If the model uses no citations when citations are required, the system attempts repair.
- If repair fails, the answer returns a warning and should not pretend to be fully cited.
- Chat previews may truncate content, but they must not corrupt citation markers.

### 6.3 Validator behavior

Minimum validator:

```text
input:
  answer_text
  source_map

output:
  valid: bool
  missing_citations: bool
  unknown_citation_ids: list[str]
  cited_source_ids: list[str]
  warnings: list[str]
```

Validation rules:

- Accept citation markers like `[1]`, `[2]`, `[1][2]`.
- Reject citation ids not present in `source_map`.
- Detect answers with no citation markers when `source_map` is non-empty and sourced answer mode is active.
- Do not require every source to be cited.

### 6.4 Repair loop

If validation fails:

1. Build a repair prompt with:
   - original answer;
   - available numbered sources;
   - instruction to preserve meaning and add only valid citations.
2. Call the model once.
3. Re-validate.
4. If still invalid, return the best answer with warnings and keep full source list visible.

Do not loop indefinitely.

### 6.5 Tests

Add cases:

- Valid answer with `[1]`.
- Valid answer with `[1][2]`.
- Invalid answer with `[99]`.
- Invalid sourced answer with no citations.
- Repair success.
- Repair failure returns warning.
- Preview truncation does not split a citation marker.

Run:

```bash
.venv/bin/python -m pytest tests/test_rag_bridge.py tests/test_rag_file_output.py -q
.venv/bin/python -m pytest tests/ -q
```

### 6.6 Risks

- Over-strict validation may reject useful answers.
- Citation repair can add latency.
- Source numbering must stay stable across full answer, preview, and exported report.

### 6.7 Rollback

- Feature-flag repair if needed.
- Validator can remain in warning-only mode before it blocks or repairs answers.

---

## 7. Phase 5: Typed Extractor Failures

**Purpose:** Prevent extraction failures from becoming saved knowledge content.

### 7.1 Files

Expected changes:

- Modify `agents/extractors/base.py`.
- Modify extractors:
  - `agents/extractors/link_extractor.py`
  - `agents/extractors/image_extractor.py`
  - `agents/extractors/voice_extractor.py`
  - `agents/extractors/document_extractor.py`
  - `agents/extractors/xiaohongshu_extractor.py`
- Modify `agents/capture_agent.py`.
- Add or modify extractor and capture tests.

### 7.2 Contract

Extractor results should distinguish:

- success with extracted text;
- success with partial warnings;
- unsupported input;
- external service failure;
- parse failure;
- authentication/configuration failure;
- no useful content extracted.

Only successful extraction should flow into normal capture drafting. Failures should produce clear user-facing warnings and confirmable next actions.

### 7.3 Suggested data model

```python
@dataclass
class ExtractionFailure:
    code: Literal[
        "unsupported",
        "network_error",
        "auth_error",
        "parse_error",
        "empty_content",
        "external_service_error",
    ]
    message: str
    retryable: bool = False
```

```python
@dataclass
class ExtractionResult:
    text: str
    title: str | None = None
    source_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    failure: ExtractionFailure | None = None
```

Match exact style to existing code.

### 7.4 Capture behavior

Rules:

- If an extractor says `unsupported`, try the next extractor.
- If an extractor says external/auth/parse failure for a supported input, stop the chain unless a deterministic fallback is safe.
- If fallback is used, add warning metadata.
- Do not save placeholder strings like `[OCR失败]` or `[网页解析失败]` as the source body unless the user explicitly supplied that text.
- For plain pasted text, keep current behavior.

### 7.5 Tests

Add cases:

- Link extractor network failure does not become draft body.
- Image OCR auth failure returns warning/error, not a capture note.
- Voice ASR failure returns warning/error, not a capture note.
- Unsupported extractor falls through to the next extractor.
- Plain text fallback still works.
- Xiaohongshu partial image OCR warning can preserve successfully extracted text.

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py tests/test_xiaohongshu_extractor.py -q
.venv/bin/python -m pytest tests/ -q
```

### 7.6 Risks

- Some existing fallback behavior may be relied on in daily use.
- Too many hard stops can make capture feel brittle.
- Gateway media metadata may interact with extractor selection.

### 7.7 Rollback

- Keep compatibility helpers that convert old extractor returns into the new shape.
- Start by warning-only mode if hard stops are too disruptive.

---

## 8. Phase 6: Context and Repository Boundary Cleanup

**Purpose:** Reduce repeated initialization and direct SQL drift.

### 8.1 Files

Expected changes:

- Add `bridge/context.py` or similar.
- Gradually update bridge modules:
  - `bridge/rag_bridge.py`
  - `bridge/reading_bridge.py`
  - `bridge/garden_bridge.py`
  - `bridge/theme_bridge.py`
  - `bridge/capture_bridge.py`
- Add repository methods where repeated direct SQL exists.

### 8.2 Contract

Introduce a lightweight context object:

```python
@dataclass
class KnowledgeContext:
    config: dict[str, Any]
    db: sqlite3.Connection
    vault_path: Path
```

Requirements:

- FastAPI routes can create and pass context.
- Runtime tool wrappers can still call bridge functions without manually creating context.
- Existing public function signatures remain backward-compatible where runtime depends on them.

### 8.3 Migration rule

For each bridge:

1. Add optional `context` parameter.
2. If absent, fall back to current `load_config()` / `init_db()` behavior.
3. Move repeated repository setup behind helper functions.
4. Add tests for both explicit context and default context paths if practical.

### 8.4 Verification

```bash
.venv/bin/python -m pytest tests/test_api_main.py tests/test_api_routes.py -q
.venv/bin/python -m pytest tests/test_rag_bridge.py tests/test_reading_bridge.py tests/test_garden_bridge.py tests/test_theme_bridge.py -q
.venv/bin/python -m pytest tests/ -q
```

### 8.5 Risks

- Runtime tool wrappers may call bridge functions directly and rely on current defaults.
- A shared connection can introduce lifecycle issues if used carelessly.
- Refactoring direct SQL can accidentally change query semantics.

### 8.6 Rollback

- Because `context` is optional, revert bridge-by-bridge.
- Do not remove old initialization helpers until all runtime callers are confirmed.

---

## 9. Phase 7: Product and Runtime UX Alignment

**Purpose:** Align the user-facing product around one coherent Knowledge Workbench.

### 9.1 Scope decision

Do not invest heavily in `/Users/pipi/hermes-custom/dashboard` unless the project explicitly revives it. It is deprecated and should remain a historical/prototype surface.

User-facing work should happen in Hermes runtime dashboard or tool descriptions.

### 9.2 UX contract

The main product surface should make these visible:

- current conversation or reading session;
- pending drafts;
- selected source material;
- cited sources;
- tool/status trace at a human-readable level;
- confirmable write actions;
- saved artifacts.

The user should not need to pick from internal tools such as Theme scan or Canvas generation during normal use.

### 9.3 Recommended layout

```text
Knowledge Workbench
  Left rail:
    pending drafts
    recent captures
    recent cards
    recent sessions

  Center:
    chat / reading discussion
    draft review
    full answer or exported report

  Right inspector:
    sources
    tool/status trace
    saved artifacts
    warnings
```

### 9.4 Runtime integration points

Expected runtime repo checks:

- Tool descriptions for Capture, Reading, RAG, Knowledge Card commit.
- Default toolset visibility.
- Dashboard route for `/knowledge-chat`, `/capture-review`, and `/cards-board`.
- Whether Theme and Canvas are hidden from the normal personal assistant path.

### 9.5 Verification journeys

Run these as real product smoke tests:

1. Long material capture:
   - paste or submit source;
   - produce draft;
   - revise draft;
   - commit;
   - verify Markdown and SQLite row;
   - ask RAG question and cite the new source.

2. Sourced Q&A:
   - ask a knowledge-base question;
   - verify cited answer;
   - save answer draft;
   - revise;
   - commit to durable knowledge.

3. Reading to Knowledge Card:
   - start reading session;
   - discuss a source;
   - summarize as Knowledge Card;
   - review draft;
   - commit;
   - verify `garden/*.md` and `garden_cards`.

4. Failure path:
   - simulate extractor failure;
   - verify no failed placeholder text is written as knowledge;
   - verify user-facing warning and next action.

### 9.6 Risks

- UI work can mask unresolved backend contracts.
- Exposing tool traces too literally can make the product feel like a debug panel.
- Hiding advanced capabilities too aggressively may make power workflows harder.

### 9.7 Rollback

- Keep old runtime routes available while introducing the new workbench flow.
- Avoid deleting advanced tools; reduce default visibility instead.

---

## 10. Execution Cadence

Recommended batch size:

- Batch 1: Phase 0 and Phase 1.
- Batch 2: Phase 2 and Phase 4 validator in warning-only mode.
- Batch 3: Phase 3 for Garden and Reading only.
- Batch 4: Phase 3 for Answer and Capture.
- Batch 5: Phase 5 extractor failures.
- Batch 6: Phase 6 context/repo cleanup.
- Batch 7: Phase 7 runtime UX alignment.

Each batch should end with:

```bash
cd /Users/pipi/hermes-custom
.venv/bin/python -m pytest tests/ -q
git status --short
```

If runtime repo is touched, also run its focused tool registry/dashboard tests.

---

## 11. Review Checklist

Before accepting any implementation batch:

- [ ] Does every changed line trace to the active phase?
- [ ] Are secrets excluded from docs, logs, commits, and test fixtures?
- [ ] Does a user-facing write still require confirmation?
- [ ] Do result envelopes still expose status, artifacts, source map, warnings, and next actions?
- [ ] Are source citations valid or explicitly warned about?
- [ ] Are extractor failures typed rather than saved as content?
- [ ] Did tests run in the intended environment?
- [ ] Was the runtime/gateway restarted only when the phase actually changed runtime behavior?
- [ ] Were spawned subagents closed after completion or error?

---

## 12. Stop Conditions

Stop and reassess if any of these happen:

- Full test suite starts failing in unrelated areas after a small phase.
- A phase requires renaming database tables or moving existing vault files.
- Runtime dashboard behavior depends on hidden state not represented in this repo.
- Citation repair changes answer meaning.
- Extractor failure hard stops make common capture workflows unusable.
- The implementation starts adding new user-facing features instead of stabilizing existing contracts.

---

## 13. Completion Audit

Implementation status as of 2026-06-19:

- Phase 0 through Phase 7 are complete.
- Default runtime paths remain backward-compatible; explicit `KnowledgeContext` is optional.
- Durable writes still require confirmable actions.
- Theme and Canvas remain available through `knowledge-advanced`, not the normal personal-assistant path.
- Latest `hermes-custom` full-suite verification: `.venv/bin/python -m pytest tests/ -q` -> `600 passed, 2 skipped`.
- Latest Hermes runtime focused verification: Knowledge product/tool/API tests -> `58 passed, 2 warnings`; `web/npm run build` -> passed.
- Runtime restart verification: gateway service is loaded; dashboard is running on `127.0.0.1:9119`; `/knowledge-chat`, `/capture-review`, and `/cards-board` return `200 text/html`.

Known residual risk:

- A full runtime `tests/hermes_cli/test_web_server.py` run still has unrelated PTY websocket failures in this local environment. Knowledge-specific web server tests passed after the Phase 7 API fix.
