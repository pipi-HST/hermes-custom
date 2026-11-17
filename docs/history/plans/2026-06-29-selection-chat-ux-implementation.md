---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Selection Chat UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the confirmed side-panel AI behavior: mode-aware retrieval, article-grounded context, stable citations, and predictable retry/supplement actions.

**Architecture:** Backend `bridge/web_context_answer.py` owns deterministic mode contracts, prompt context, citation cleanup, and trace fields. Frontend `KnowledgeLibraryPage.tsx` owns side-panel request context, mode-aware process UI, footer actions, and streaming source retention. `KnowledgeAnswerView.tsx` owns source-list labeling based on whether inline citations exist.

**Tech Stack:** Python bridge tests with `pytest`; React/TypeScript frontend with Vite build; existing Hermes web API types.

---

### Task 1: Backend Mode Contract And Article Context

**Files:**
- Modify: `/Users/pipi/hermes-custom/bridge/web_context_answer.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_web_context_answer.py`

- [ ] **Step 1: Add tests for hard mode behavior**

Add tests that assert:
- `current_article_only` does not call local or web adapters, strips `[K]` and `[W]`, and returns empty source lists.
- `local_only` always calls the local adapter and never calls web search.
- `web_only` always calls web search even when the planner tries to skip.
- `local_web` always calls local search, but can skip web when planner returns `needs_web=false`.

- [ ] **Step 2: Implement mode decision helpers**

In `bridge/web_context_answer.py`, add a small deterministic planner result object with `complexity`, `question_type`, `needs_web`, `needs_local`, `search_budget`, `queries`, and `reason`. Clamp mode behavior after parsing planner output.

- [ ] **Step 3: Normalize article context**

Build a shared article-context package:
- short article `<=4000` chars: full article body.
- long article: summary `<=800` plus current section body.
- section lookup failure: summary `<=800` plus `surrounding_before` and `surrounding_after` capped at 500 each.
- overlong section: clip to 4000 and add trace warning.

- [ ] **Step 4: Update prompts**

Use the normalized article context for both query planning and answer generation. Do not send file path, folder path, or tags as primary prompt context.

- [ ] **Step 5: Run backend focused tests**

Run:

```bash
cd /Users/pipi/hermes-custom
.venv/bin/python -m pytest tests/test_web_context_answer.py -q
```

Expected: pass.

### Task 2: Backend Trace, Query Budget, And Citations

**Files:**
- Modify: `/Users/pipi/hermes-custom/bridge/web_context_answer.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_web_context_answer.py`

- [ ] **Step 1: Add query-budget tests**

Add tests that assert:
- `search_budget` is a maximum, not a target count.
- queries are not executed when `needs_web=false`.
- planned-but-skipped queries remain visible in trace.
- partial timeout warnings appear in response warnings and trace, not answer text.

- [ ] **Step 2: Preserve source maps**

Ensure final answers never contain `[K]` or `[W]` refs without matching final sources. For `current_article_only`, remove all `[K]` and `[W]` refs and return no sources.

- [ ] **Step 3: Add trace fields**

Expose `complexity`, `question_type`, `needs_web`, `needs_local`, `search_budget`, `queries`, `reason`, `article_context_source`, `used_article_context`, `used_local`, `web_search_attempted`, `used_web`, `query_statuses`, `warnings`, `total_elapsed_ms`, and `stage_timings.search_ms`.

- [ ] **Step 4: Run backend tests**

Run:

```bash
cd /Users/pipi/hermes-custom
.venv/bin/python -m pytest tests/test_web_context_answer.py tests/test_research_bridge.py -q
```

Expected: pass or fail only on unrelated existing dirty work; fix failures caused by this implementation.

### Task 3: Frontend Request Context And Footer Actions

**Files:**
- Modify: `/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeLibraryPage.tsx`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/web/src/lib/api.ts`

- [ ] **Step 1: Store original request context**

Extend `ArticleChatMessage` with a `requestContext` containing original question, selected text, surrounding context, heading path, selection offsets, confidence, and mode.

- [ ] **Step 2: Use request context for retry actions**

Change `重新检索知识库`, `重新联网检索`, and `联网补充` so they create a new assistant answer from the message's saved `requestContext`. Do not read the current input box or current page selection.

- [ ] **Step 3: Make footer mode-aware**

Render footer retrieval action as:
- current article: no retrieval action.
- knowledge base: `重新检索知识库`.
- web: `重新联网检索`.
- knowledge base plus web with web attempted: `重新联网检索`.
- knowledge base plus web with web skipped: `联网补充`.

- [ ] **Step 4: Preserve existing stop and Enter behavior**

Keep the existing stop button and Enter-to-send behavior. Loading Enter must not duplicate a request.

### Task 4: Frontend Process UI And Citation Rendering

**Files:**
- Modify: `/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeLibraryPage.tsx`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/web/src/components/KnowledgeAnswerView.tsx`

- [ ] **Step 1: Remove source truncation from data path**

Change `mergeResearchSources` so it does not slice to five. Keep any display slicing inside source-list UI only.

- [ ] **Step 2: Make process UI stage-aware**

Only show web details when web was attempted or web data exists. Hide web backend rows in current-article and knowledge-base modes. Show article/local/web/generation groups only when active.

- [ ] **Step 3: Rename source list**

In `KnowledgeAnswerView`, label the source list `引用来源` when inline `[K]/[W]` citations exist, and `使用来源` when sources exist without inline citations.

- [ ] **Step 4: Add missing-source fallback copy**

If an inline citation lacks a matching source, render safely and expose a user-readable diagnostic in retrieval details.

### Task 5: Verification

**Files:**
- Verify only; modify implementation files only if tests/build fail because of this work.

- [ ] **Step 1: Run backend tests**

```bash
cd /Users/pipi/hermes-custom
.venv/bin/python -m pytest tests/test_web_context_answer.py tests/test_research_bridge.py -q
```

- [ ] **Step 2: Run frontend build**

```bash
cd /Users/pipi/Downloads/hermes-agent-main/web
npm run build
```

- [ ] **Step 3: Inspect final diffs**

Confirm changes are limited to the intended backend, frontend, tests, and this plan document. Do not revert unrelated dirty files.
