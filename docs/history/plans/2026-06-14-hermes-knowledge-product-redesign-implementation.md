---
status: historical
execution_status: done
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Hermes Knowledge Product Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the P1-P4 redesign from `docs/superpowers/specs/2026-06-14-hermes-knowledge-product-redesign.md` so Hermes Knowledge works as a coherent reading-discussion-to-knowledge pipeline.

**Architecture:** Keep Hermes main as the user-facing orchestrator and `hermes-custom` as the knowledge capability layer. Reading Session owns temporary discussion state; Capture owns source material drafts; Library owns maintenance/indexing; RAG owns sourced answers; Knowledge Cards own reusable knowledge objects. Theme remains internal and Canvas is removed from the main user path.

**Tech Stack:** Python 3.11, SQLite, pytest, Obsidian Markdown vault, Hermes tool wrappers in `/Users/pipi/Downloads/hermes-agent-main`, optional React/Vite dashboard in Hermes web.

## Execution Status

Updated: 2026-06-14

- P1/P2/P4 core path implemented: Reading Session can clarify 3 output types, create reviewable Knowledge Card drafts, and confirmed Reading cards now write both `garden/*.md` and `garden_cards` DB rows.
- P0/P2 user-facing route cleanup implemented in Hermes main: Theme and Canvas are hidden from `personal-assistant` and preserved under `knowledge-advanced`; personal assistant skill routing now points ordinary users to Reading/Capture/RAG/Knowledge Cards.
- Verification passed:
  - `/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/ -q` → `510 passed, 2 skipped`
  - `/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest tests/test_toolsets.py tests/tools/test_knowledge_product_tools.py tests/tools/test_registry.py tests/tools/test_garden_draft_tools.py tests/tools/test_generate_canvas_tool.py tests/tools/test_theme_candidates_tool.py -q` → `73 passed`
- Broader `/Users/pipi/Downloads/hermes-agent-main tests/tools` still has unrelated pre-existing/environment failures in file safety, MCP async, Vision, Daytona, etc.; product-specific failures from this redesign were fixed.
- Hermes was restarted with sessions cleared; dashboard `http://127.0.0.1:9119/` returns HTML and gateway logs show Weixin/Feishu connected.

---

## File Map

- Modify `bridge/reading_bridge.py`: change clarification choices, add `knowledge_card` output type, route reading card drafts to Garden/Card logic, and make user-facing text match the redesign.
- Modify `db/reading_repo.py`: keep existing schema use; only add helper methods if needed for listing latest open reading drafts.
- Modify `agents/garden_agent.py`: rename user-facing Garden language to Knowledge Cards, remove `question` as a generated card kind, map legacy `caution` to product-facing `risk`, simplify card body structure.
- Modify `bridge/garden_bridge.py`: rename previews and commit messages to Knowledge Cards, keep backward-compatible DB table names.
- Modify `/Users/pipi/Downloads/hermes-agent-main/tools/reading_session_tool.py`: update tool schema/description so the main Agent routes reading discussions and card creation correctly.
- Modify `/Users/pipi/Downloads/hermes-agent-main/tools/synthesize_garden_tool.py`, `revise_garden_draft_tool.py`, `commit_garden_draft_tool.py`: update user-facing descriptions from Garden to Knowledge Cards.
- Modify `/Users/pipi/Downloads/hermes-agent-main/tools/generate_canvas_tool.py` and toolset registration: hide Canvas from normal personal-assistant flows while keeping code importable.
- Modify `/Users/pipi/Downloads/hermes-agent-main/toolsets.py` or `toolsets_distributions.py`: remove Canvas and Theme from the default visible knowledge flow if currently present.
- Add/modify tests in `tests/test_reading_bridge.py`: P1 behavior for clarification and `knowledge_card`.
- Add/modify tests in `tests/test_garden_agent.py` and `tests/test_garden_bridge.py`: P2 card kind constraints and user-facing wording.
- Add/modify tests in `/Users/pipi/Downloads/hermes-agent-main/tests/tools/`: tool descriptions no longer promote Theme/Canvas and Reading lists the new output choices.

## Task 1: Reading Session Output Contract

**Files:**
- Modify: `/Users/pipi/hermes-custom/bridge/reading_bridge.py`
- Test: `/Users/pipi/hermes-custom/tests/test_reading_bridge.py`

- [x] **Step 1: Write failing tests for the new clarification choices**

Add tests asserting `summarize_reading_session(..., output_type="")` offers exactly:

```text
1. 原文增强版主笔记
2. 局部学习笔记
3. 知识卡片
```

and does not offer `讨论札记`.

- [x] **Step 2: Run the focused test**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_reading_bridge.py -q
```

Expected: the new test fails because the bridge still offers `讨论札记`.

- [x] **Step 3: Implement the minimal contract change**

Change `CLARIFY_OUTPUT_TYPE` and `OUTPUT_TYPES` in `bridge/reading_bridge.py`:

```python
CLARIFY_OUTPUT_TYPE = (
    "你想整理成哪种？\n"
    "1. 原文增强版主笔记：保留文章主线，把刚才讨论补进去\n"
    "2. 局部学习笔记：只整理这次卡住的概念/段落\n"
    "3. 知识卡片：提炼成以后可复用的观点、方法、事实或风险\n"
    "回复 1/2/3 即可。"
)

OUTPUT_TYPES = {
    "enhance_capture": "原文增强版主笔记",
    "local_learning_note": "局部学习笔记",
    "knowledge_card": "知识卡片",
}
```

Update `_normalize_output_type()` so `3`, `卡片`, `知识卡片`, `做成卡片`, and `knowledge_card` normalize to `knowledge_card`; keep legacy `discussion_note` accepted only as `local_learning_note` for backward compatibility.

- [x] **Step 4: Run the focused test**

Run the same pytest command. Expected: PASS.

## Task 2: Reading Session to Knowledge Card Draft

**Files:**
- Modify: `/Users/pipi/hermes-custom/bridge/reading_bridge.py`
- Test: `/Users/pipi/hermes-custom/tests/test_reading_bridge.py`

- [x] **Step 1: Write failing tests for `knowledge_card` draft creation**

Add a test that creates a Reading Session, records a discussion turn, calls `summarize_reading_session(output_type="knowledge_card")`, and asserts:

- result status is success;
- durable artifact type is `reading_draft`;
- `meta.output_type == "knowledge_card"`;
- `draft_text` contains the user question, assistant answer, and source evidence;
- chat preview says `知识卡片草稿`.

- [x] **Step 2: Implement draft text builder support**

Update `_draft_title()`, `_build_draft_text()`, `_commit_reading_artifact()`, and `_commit_independent_note()`:

- `knowledge_card` creates a reviewable Reading Draft first;
- committing `knowledge_card` writes to `garden/` through existing Knowledge Card/Garden draft flow where possible, or creates a `garden` Markdown card with `type: garden_card` if no capture ids are available;
- do not write to `reading/` as a discussion note.

Minimum viable implementation:

```python
if output_type == "knowledge_card":
    return _commit_knowledge_card(vault_path, repo, draft)
```

The first version can write a Markdown card from the reading draft body, with frontmatter `type: garden_card`, `card_kind: viewpoint`, `status: seed`, and `reading_session_id`.

- [x] **Step 3: Run Reading tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_reading_bridge.py -q
```

Expected: PASS.

## Task 3: Knowledge Cards Naming and Type Contract

**Files:**
- Modify: `/Users/pipi/hermes-custom/agents/garden_agent.py`
- Modify: `/Users/pipi/hermes-custom/bridge/garden_bridge.py`
- Test: `/Users/pipi/hermes-custom/tests/test_garden_agent.py`
- Test: `/Users/pipi/hermes-custom/tests/test_garden_bridge.py`

- [x] **Step 1: Write failing tests for product-facing card kinds**

Add tests asserting:

- `question` from the LLM normalizes to `viewpoint` or another valid non-question fallback;
- valid card kinds are `viewpoint`, `fact`, `method`, `risk`;
- legacy `caution` still works but normalizes/stores as `risk`;
- chat previews say `Knowledge Card` or `知识卡片`, not `Garden 草稿` or `知识花园`.

- [x] **Step 2: Update card constants**

Change:

```python
CARD_KINDS = {"viewpoint", "fact", "method", "risk"}
```

Map legacy input:

```python
if kind == "caution":
    return "risk"
if kind == "question":
    return "viewpoint"
```

Update labels:

```python
CARD_KIND_LABELS = {
    "viewpoint": "观点卡",
    "fact": "事实卡",
    "method": "方法卡",
    "risk": "风险卡",
}
```

- [x] **Step 3: Simplify card markdown**

Replace type-specific rigid templates with the shared structure:

```markdown
# 标题

一句话说明...

## 内容

...

## 适用或启发

...

## 来源

- [[来源材料标题]]
```

Use existing `core_arguments` and `key_materials` to fill `内容`; use source links already produced by `_format_capture_source()`.

- [x] **Step 4: Run Garden tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_garden_agent.py /Users/pipi/hermes-custom/tests/test_garden_bridge.py -q
```

Expected: PASS after updating old assertions that expected `caution`/Garden wording.

## Task 4: Hide Theme and Canvas from the Main User Path

**Files:**
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/theme_scan_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/theme_candidates_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/generate_canvas_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/toolsets.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/toolset_distributions.py`
- Test: `/Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py`

- [x] **Step 1: Inspect current toolset membership**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
rg -n "theme_scan|theme_candidates|generate_canvas|synthesize_garden|reading_session" toolsets.py toolset_distributions.py tests/test_toolsets.py
```

- [x] **Step 2: Remove Canvas from default personal assistant toolset**

If `generate_canvas` is in the default personal-assistant or knowledge path, remove it from that default list. Keep the tool file importable for manual/debug use.

- [x] **Step 3: Soften Theme tool descriptions**

Update Theme descriptions to say it is an internal/advanced association discovery tool and should not be suggested as the default next step for ordinary reading, capture, or RAG tasks.

- [x] **Step 4: Run toolset tests**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest tests/test_toolsets.py tests/tools -q
```

Expected: PASS or only unrelated pre-existing failures, which must be recorded.

## Task 5: Hermes Tool Wording for Reading and Cards

**Files:**
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/reading_session_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/synthesize_garden_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/revise_garden_draft_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/commit_garden_draft_tool.py`
- Test: `/Users/pipi/Downloads/hermes-agent-main/tests/tools/`

- [x] **Step 1: Update tool descriptions**

Descriptions should tell the main Agent:

- use Reading Session when the user is trying to understand a material, not when they clearly ask to capture it;
- discussion does not automatically write Obsidian;
- when user asks to organize the discussion, call reading summarization and clarify output type if needed;
- Knowledge Cards are reusable viewpoint/method/fact/risk objects, not summaries.

- [x] **Step 2: Add/adjust tests for schema text**

Add lightweight tests that inspect tool definitions and assert:

- Reading output choices include `knowledge_card`;
- Knowledge Card tool descriptions do not say `Garden` as the primary user-facing term;
- Canvas is not included in default toolset.

- [x] **Step 3: Run focused Hermes-main tests**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest tests/tools/test_registry.py tests/test_toolsets.py -q
```

Expected: PASS.

## Task 6: P4 Data and Observability Checks

**Files:**
- Modify: `/Users/pipi/hermes-custom/scripts/library_maintenance.py` if current consistency checks miss Reading/Card artifacts.
- Modify: `/Users/pipi/hermes-custom/bridge/reading_bridge.py` if run ledger input/output is incomplete.
- Test: `/Users/pipi/hermes-custom/tests/test_reading_bridge.py`
- Test: `/Users/pipi/hermes-custom/tests/test_library_agent.py`

- [x] **Step 1: Verify run ledger coverage**

Run a local Reading Session flow and inspect `agent_run_events` rows for `reading`.

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_agent_run_bridge.py /Users/pipi/hermes-custom/tests/test_reading_bridge.py -q
```

- [x] **Step 2: Add tests only if a gap is found**

If Reading or Knowledge Card commit does not produce ledger entries or durable artifacts, add a failing test around the missing envelope metadata.

- [x] **Step 3: Run consistency checks**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_library_agent.py /Users/pipi/hermes-custom/tests/test_schema.py -q
```

Expected: PASS.

## Task 7: Full Verification and Hermes Restart

**Files:**
- No code changes unless verification fails.

- [x] **Step 1: Run hermes-custom focused suite**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_reading_bridge.py \
  /Users/pipi/hermes-custom/tests/test_garden_agent.py \
  /Users/pipi/hermes-custom/tests/test_garden_bridge.py \
  /Users/pipi/hermes-custom/tests/test_rag_bridge.py \
  /Users/pipi/hermes-custom/tests/test_library_agent.py \
  -q
```

- [x] **Step 2: Run broader hermes-custom suite if focused suite passes**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/ -q
```

- [x] **Step 3: Run Hermes-main focused tests**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest tests/test_toolsets.py tests/tools/test_registry.py -q
```

- [x] **Step 4: Restart Hermes with clean sessions**

Use the existing Hermes restart hygiene workflow:

- backup/clear `/Users/pipi/.hermes/sessions`;
- restart gateway/dashboard processes;
- verify gateway logs and dashboard route.

- [ ] **Step 5: Manual smoke test checklist**

Ask the user to test:

1. Send a material and ask a reading question; expected: Reading Session, no capture draft.
2. Say “整理进笔记”; expected: choices include main note, local note, knowledge card.
3. Choose knowledge card; expected: reviewable card draft, not direct write.
4. Confirm card; expected: file under `garden/`, readable source links.
5. Ask a RAG question about it; expected: answer cites readable source titles.
