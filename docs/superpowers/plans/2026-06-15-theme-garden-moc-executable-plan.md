# Theme Garden MOC Executable Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse the existing Theme Agent code as an Obsidian-native theme map layer, and tighten the frontend into a clear Knowledge Workbench: Theme automatically creates and updates `themes/*.md`, Garden turns selected theme directions into reviewable Knowledge Card drafts, and Capture Review is folded into the main workbench instead of remaining a competing page.

**Architecture:** Keep the current index-first Theme scan and `pending_questions -> synthesize_garden(question_id)` flow. Refactor the old MOC writer into a safer Theme page writer with managed Hermes sections and a preserved user section. Garden commit writes provenance back to the source Theme page so repeated scans do not create duplicate cards. On the frontend, `/knowledge-chat` becomes the single Knowledge Workbench that contains chat, Reading Session, draft review, source viewing, and tool status; Cards Board remains the separate result surface.

**Tech Stack:** Python, SQLite, Obsidian Markdown vault, existing Hermes result envelopes, existing `capture_embeddings`, pytest, Hermes runtime tool registry.

---

## 1. Product Contract

### 1.1 Folder Semantics

```text
captures/
  Single-material notes. Capture Agent owns generation; Library owns structure and index maintenance.

themes/
  Theme maps / MOCs. Theme Agent owns automatic relationship discovery and page updates.
  These pages help humans read across materials and help Garden decide what can be carded.

garden/
  Knowledge Cards. Garden Agent owns reviewable reusable knowledge objects.
  Cards can originate from a Capture, a Reading Session, or a Theme direction.
```

### 1.2 Agent Boundaries

- Capture Agent handles material ingestion, extraction intent, and draft generation.
- Library Agent maintains file and DB health, outline, sections, and embeddings.
- Theme Agent discovers relationships between multiple Captures and automatically maintains `themes/*.md`.
- Garden Agent turns a selected direction into a Knowledge Card draft, then writes `garden/*.md` only after user confirmation.
- RAG reads Capture, Theme, and Garden as different evidence layers; it does not mutate them.

### 1.3 User-Facing Principle

The user should not need to think in internal Agent names. Product language should be:

- "保存/整理材料" for Capture.
- "整理主题关联" for Theme.
- "沉淀知识卡片" for Garden.
- "基于知识库回答" for RAG.

### 1.4 Frontend Surface Contract

The frontend should stop presenting Knowledge Chat and Capture Review as two equal work surfaces. They overlap because both are part of the same user journey: ask or submit material, generate a draft, inspect the result, revise it, and commit it.

Use this product structure:

```text
Knowledge Workbench
  Chat, file upload, Reading Session, RAG answers, draft review, source view,
  revision input, commit actions, citations, tool calls, and material status.

Knowledge Cards
  The result library for committed Garden cards. Browse, filter, inspect, and reuse cards.

System / Logs
  Operational view for gateway state, run ledger, tool calls, errors, and debugging.
```

Rules:

- `/knowledge-chat` should become the main `Knowledge Workbench`.
- The standalone `Capture Review` page should be hidden, removed from primary navigation, or redirected after its functionality is merged.
- Draft review remains a first-class capability, but as a Workbench mode rather than a separate page.
- Cards Board remains independent because it is a library of committed results, not a work-in-progress queue.
- System/log pages remain available as admin/debug surfaces, not as knowledge-product primary pages.

Workbench layout:

```text
Top bar
  Current material, current session, connection state, active mode.

Left rail
  Current conversations, pending drafts, recent materials, recent commits, recent cards.

Center workspace
  Chat Mode: conversation, file input, RAG answer, Reading Session.
  Draft Review Mode: refined draft, raw/source text, metadata, revision input, commit actions.
  Source Mode: full extracted source, OCR, document text, or raw archive.
  Result Mode: long answer, exported report, full citations.

Right inspector
  Sources tab: actual cited Capture/Theme/Garden titles and snippets.
  Tools tab: tool calls, inputs summary, status, generated artifacts.
  Status tab: extraction, draft, commit, Library maintenance, embedding, Theme scan, Garden sync.
```

Interaction contract:

- If a user submits a material and Hermes creates a draft, the Workbench should switch to Draft Review Mode automatically.
- If a user selects a pending draft in the left rail, the center workspace should show Draft Review Mode.
- The bottom input should change its hint based on mode:
  - Chat Mode: ask, upload, paste material, or request knowledge-base answers.
  - Draft Review Mode: write revision instructions or confirm commit.
- `确认入库`, `继续修改`, and `转知识卡片` belong in Draft Review Mode, not in a separate page.
- Tool state should be visible without making the user read raw logs.

---

## 2. Obsidian File Contracts

### 2.1 `themes/*.md` Contract

Theme pages are automatic MOC-style relationship maps. They must be readable in Obsidian and safe to re-run.

```markdown
---
type: theme
status: active
theme_id: theme_ai_agent_engineering
theme_name: AI Agent 工程化
updated: '2026-06-15T10:30:00+08:00'
related_captures:
  - captures/Building Effective AI Agents-2026-05-30-c4bffa9d.md
related_garden_cards:
  - garden/AI Agent 工程化：从单次对话到并行协作团队-xxxx.md
---

# AI Agent 工程化

<!-- HERMES-THEME:START -->
## 主题判断

这一组材料共同讨论的是：AI Agent 如何从单次调用，演化成可持续协作、可评估、可治理的工程系统。

## 相关材料

- [[Building Effective AI Agents]] — 提供 agent/workflow 的基础分类。
- [[Opus 4.8发布：41天补丁包里，Anthropic藏了一次战略转向]] — 提供多 agent 协作和工程实践案例。

## 材料之间的关系

- [[Building Effective AI Agents]] 提供框架，[[Opus 4.8发布：41天补丁包里，Anthropic藏了一次战略转向]] 提供案例。
- 前者强调从简单 workflow 起步，后者展示复杂多 agent 协作的现实需求。

## 可沉淀的知识卡片方向

- 方法卡：如何判断什么时候需要 agent workflow。
- 风险卡：多 agent 系统为什么容易失控。
- 观点卡：AI 工程化的核心不是模型更强，而是流程更可控。

## 已沉淀的知识卡片

- [[AI Agent 工程化：从单次对话到并行协作团队]]
<!-- HERMES-THEME:END -->

## 我的补充

这里留给用户手动写。Theme Agent 不覆盖这一节。
```

Rules:

- Theme Agent may automatically create and update `themes/*.md`.
- Theme Agent updates only the `HERMES-THEME` managed block and frontmatter fields it owns.
- Theme Agent must never overwrite `## 我的补充`.
- `已沉淀的知识卡片` is a state-tracking section, not the main content.
- `related_garden_cards` stays because it prevents duplicate Garden Card generation and keeps Theme -> Garden traceability.
- Existing capture wiki-link injection and `linked_themes` tagging may remain, but they must be deduplicated and must not rewrite unrelated Capture content.

### 2.2 `garden/*.md` Contract

Garden cards are reusable knowledge objects, not mini MOCs.

```markdown
---
type: knowledge_card
card_kind: viewpoint
status: seed
title: AI Agent 工程化：从单次对话到并行协作团队
summary: AI Agent 的关键变化不是单次回答更强，而是工程协作流程变得可组织、可评估、可治理。
source_theme: themes/ai-agent-engineering.md
sources:
  - captures/Building Effective AI Agents-2026-05-30-c4bffa9d.md
---

# AI Agent 工程化：从单次对话到并行协作团队

一句话说明这张卡片的核心价值。

## 内容

自然展开，不强行套模板。

## 适用或启发

说明以后什么时候会调用这张卡。

## 来源

- [[Building Effective AI Agents]]
```

Rules:

- Garden Card may record `source_theme`.
- Theme page also records the Garden Card in `related_garden_cards`.
- If a Garden Card came from a Theme direction, `commit_garden_draft` should mark the pending Theme question answered and update the Theme page state.

---

## 3. Existing Code To Reuse

Keep and reuse:

- `/Users/pipi/hermes-custom/agents/theme_graph.py`
  - deterministic index-first relationship graph.
- `/Users/pipi/hermes-custom/bridge/theme_bridge.py`
  - `run_theme_scan`
  - `list_theme_candidates`
  - `_build_theme_nodes`
  - graph cluster to legacy dict conversion.
- `/Users/pipi/hermes-custom/agents/theme_agent.py`
  - `generate_theme_name`
  - `generate_question`
  - LLM explanation ability.
- `/Users/pipi/hermes-custom/bridge/garden_bridge.py`
  - `synthesize_garden(question_id=...)`
  - `commit_garden_draft`
- `/Users/pipi/Downloads/hermes-agent-main/tools/theme_scan_tool.py`
  - tool schema, but update wording.
- `/Users/pipi/Downloads/hermes-agent-main/tools/theme_candidates_tool.py`
  - candidate listing, but update wording.

Refactor, do not delete:

- `agents.theme_agent.build_moc`
  - keep compatibility wrapper if needed.
  - new semantic name should be `build_theme_page`.
- `agents.markdown_writer.write_moc`
  - keep compatibility wrapper if tests or callers still use it.
  - new semantic name should be `write_theme_page`.
- `agents.markdown_writer.update_moc`
  - keep compatibility wrapper.
  - new semantic name should be `update_theme_page`.

Review carefully before changing:

- `bridge.theme_bridge._inject_wiki_links`
- `bridge.theme_bridge._tag_captures_with_theme`

These are useful Obsidian affordances, but they are file mutations outside `themes/`. Keep them deduplicated and limited.

---

## 4. Implementation Tasks

### Task 1: Add Theme Page Markdown Contract Tests

**Files:**
- Modify: `/Users/pipi/hermes-custom/agents/markdown_writer.py`
- Create or modify: `/Users/pipi/hermes-custom/tests/test_theme_page_writer.py`

- [ ] **Step 1: Write failing test for new Theme page creation**

Add a test that calls `write_theme_page` with three captures and one card link.

Expected assertions:

- The file is written under `themes/`.
- Frontmatter contains `type: theme`, `status: active`, `theme_name`, `related_captures`, and `related_garden_cards`.
- Body contains `<!-- HERMES-THEME:START -->` and `<!-- HERMES-THEME:END -->`.
- Body contains `## 主题判断`, `## 相关材料`, `## 材料之间的关系`, `## 可沉淀的知识卡片方向`, `## 已沉淀的知识卡片`, and `## 我的补充`.

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_page_writer.py::test_write_theme_page_creates_managed_theme_map -q
```

Expected: FAIL because `write_theme_page` does not exist yet.

- [ ] **Step 2: Write failing test for preserving user section**

Create a Theme page with existing manual text under `## 我的补充`, then call `update_theme_page`.

Expected assertions:

- The managed block changes.
- The manual text under `## 我的补充` remains byte-for-byte present.
- Existing Garden links are deduplicated.

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_page_writer.py::test_update_theme_page_preserves_user_notes -q
```

Expected: FAIL because `update_theme_page` does not exist yet.

- [ ] **Step 3: Implement `write_theme_page` and `update_theme_page`**

Implementation shape:

- Add constants:
  - `HERMES_THEME_START = "<!-- HERMES-THEME:START -->"`
  - `HERMES_THEME_END = "<!-- HERMES-THEME:END -->"`
- Use `_make_slug(theme_name)` for the filename.
- Serialize frontmatter with YAML.
- Replace only the managed block on update.
- If `## 我的补充` is missing, append it after the managed block.
- Keep `write_moc` and `update_moc` as wrappers that call the new functions.

- [ ] **Step 4: Run focused writer tests**

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_page_writer.py -q
```

Expected: PASS.

---

### Task 2: Rename MOC Generation Semantics In ThemeAgent

**Files:**
- Modify: `/Users/pipi/hermes-custom/agents/theme_agent.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_theme_agent.py`

- [ ] **Step 1: Add failing test for Theme page content shape**

Test `ThemeAgent.build_theme_page(theme_name, captures)` with `_call_llm` patched to return JSON.

Expected assertions:

- Output includes `# <theme_name>`.
- Output includes `## 主题判断`.
- Output includes `## 相关材料`.
- Output includes `## 材料之间的关系`.
- Output includes `## 可沉淀的知识卡片方向`.
- Output includes `## 已沉淀的知识卡片`.
- Output does not include old section heading `## 开放问题` unless the LLM explicitly returns meaningful open questions and the product decision reintroduces them.

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_agent.py::test_build_theme_page_outputs_new_theme_contract -q
```

Expected: FAIL because `build_theme_page` does not exist yet.

- [ ] **Step 2: Update prompt naming and output contract**

Change the MOC content prompt from a generic "主题页内容" to the new contract:

- `theme_judgment`
- `related_materials`
- `relationships`
- `card_directions`
- `settled_cards`

Keep the LLM role simple: explain relationships and candidate directions. Do not ask it to manage user notes.

- [ ] **Step 3: Implement `build_theme_page` and compatibility wrapper**

Implementation requirements:

- `build_theme_page(theme_name, captures, settled_cards=None)` returns only body markdown.
- `build_moc(theme_name, captures)` remains as a wrapper calling `build_theme_page(theme_name, captures)`.
- Fallback content must be useful when the LLM returns malformed JSON.
- No naked internal capture IDs should appear in the body.

- [ ] **Step 4: Run focused ThemeAgent tests**

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_agent.py -q
```

Expected: PASS.

---

### Task 3: Make Theme Scan Auto-Write Theme Pages With New Contract

**Files:**
- Modify: `/Users/pipi/hermes-custom/bridge/theme_bridge.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_theme_bridge.py`

- [ ] **Step 1: Add failing test for Theme scan writing a Theme page**

Patch graph clustering and LLM calls so a cluster of three captures is produced.

Expected assertions:

- `run_theme_scan()` creates or updates exactly one `themes/*.md`.
- `durable_artifacts` includes `type="theme_page"`.
- The returned `next_actions` include `intent="synthesize_garden"` and a `question_id`.
- The created pending question can later be used by `synthesize_garden(question_id=...)`.

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_bridge.py::test_theme_scan_writes_theme_page_and_candidate_action -q
```

Expected: FAIL until the bridge uses the new writer.

- [ ] **Step 2: Replace `_create_moc` internals with Theme page writer**

Keep `_create_moc` as a compatibility function name for now, but make it call:

- `agent.generate_theme_name(all_captures)`
- `agent.build_theme_page(theme_name, all_captures, settled_cards=[])`
- `write_theme_page(...)`

The function should still skip clusters smaller than 3 captures.

- [ ] **Step 3: Store Theme page path in pending question context**

When creating pending questions, set:

```text
context_ref = theme:<relative_theme_path>
```

Example:

```text
theme:themes/ai-agent-engineering.md
```

This allows Garden commit to find the source Theme page without a schema migration.

- [ ] **Step 4: Keep existing Obsidian affordances safe**

Do not remove `_inject_wiki_links` or `_tag_captures_with_theme` in this task.

Add or keep tests that prove:

- duplicate wiki links are not inserted on repeated scans;
- repeated `linked_themes` frontmatter values are deduplicated;
- missing files are skipped without crashing.

- [ ] **Step 5: Run focused bridge tests**

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_bridge.py -q
```

Expected: PASS.

---

### Task 4: Sync Garden Commit Back To Source Theme Page

**Files:**
- Modify: `/Users/pipi/hermes-custom/bridge/garden_bridge.py`
- Modify: `/Users/pipi/hermes-custom/agents/markdown_writer.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_garden_bridge.py`

- [ ] **Step 1: Add failing test for Theme-origin Garden commit**

Create:

- one pending Theme question with `context_ref="theme:themes/ai-agent-engineering.md"`;
- one Garden draft with that `question_id`;
- an existing Theme page.

Call `commit_garden_draft(draft_id)`.

Expected assertions:

- Garden card markdown is written to `garden/`.
- `pending_questions.status` becomes `answered`.
- Theme page frontmatter `related_garden_cards` contains the new card path.
- Theme page body `## 已沉淀的知识卡片` contains a wiki link to the new card.
- Re-running the update does not duplicate the card link.

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_garden_bridge.py::test_commit_theme_origin_garden_card_updates_source_theme_page -q
```

Expected: FAIL until the Garden commit path updates Theme pages.

- [ ] **Step 2: Add helper to resolve Theme path from `question_id`**

In `garden_bridge.py`, add a small helper:

```python
def _theme_path_for_question(conn, question_id: str) -> str:
    row = conn.execute("SELECT context_ref FROM pending_questions WHERE id=?", (question_id,)).fetchone()
    if not row:
        return ""
    context_ref = row["context_ref"] or ""
    if context_ref.startswith("theme:"):
        return context_ref.removeprefix("theme:").strip()
    return ""
```

- [ ] **Step 3: Add `append_garden_card_to_theme_page`**

In `markdown_writer.py`, add a focused helper that:

- reads the Theme page;
- appends a Garden card wiki link to `## 已沉淀的知识卡片` inside the managed block;
- updates `related_garden_cards` frontmatter;
- deduplicates by path and title;
- preserves `## 我的补充`.

- [ ] **Step 4: Call helper after Garden markdown is written**

In `commit_garden_draft`, after `rel_path = f"garden/{filename}"`, resolve the Theme path and update it if present.

Do not fail the Garden commit if Theme page sync fails. Instead:

- log a warning;
- include `meta.theme_sync_status = "failed"` and `meta.theme_sync_error`;
- keep card creation successful.

- [ ] **Step 5: Run focused Garden tests**

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_garden_bridge.py -q
```

Expected: PASS.

---

### Task 5: Update Runtime Tool Descriptions

**Files:**
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/theme_scan_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/theme_candidates_tool.py`
- Modify only if needed: `/Users/pipi/Downloads/hermes-agent-main/toolsets.py`

- [ ] **Step 1: Update `theme_scan` description**

The description must say:

- Theme Agent automatically creates or updates `themes/*.md`.
- Theme pages are MOC-style relationship maps.
- Theme scan should be used when the user asks to scan recent content, organize theme relationships, or find materials that can become Knowledge Cards.
- If `next_actions` returns a `question_id`, the main Agent should call `synthesize_garden(question_id=...)` when the user chooses a direction.
- Do not tell the user Theme is deprecated or only internal.

- [ ] **Step 2: Update `theme_candidates` description**

The description must say:

- It lists existing Theme candidates from previous scans.
- It should be used after "没有待扫描的新内容" when the user asks what candidates exist.
- It must preserve `question_id` routing.

- [ ] **Step 3: Run runtime tool tests**

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' /Users/pipi/Downloads/hermes-agent-main/tests/tools /Users/pipi/Downloads/hermes-agent-main/tests/agent -q
```

Expected: PASS or only unrelated pre-existing failures. If failures appear, capture exact failing test names and fix tool description/registration issues first.

---

### Task 6: Merge Capture Review Into Knowledge Workbench

**Files:**
- Inspect and modify the actual Hermes web frontend files that define `/knowledge-chat`, `/capture-review`, the sidebar navigation, and shared API clients.
- Likely frontend root: `/Users/pipi/Downloads/hermes-agent-main`
- Likely backend/custom dependency root: `/Users/pipi/hermes-custom`
- Modify docs if route names differ after inspection.
- Test: existing frontend route/API tests if present; otherwise add focused tests around route rendering and data adapters.

- [ ] **Step 1: Locate the current frontend route files**

Run:

```bash
rg -n "knowledge-chat|capture-review|Cards Board|Capture Review|Knowledge Chat|CaptureReview|KnowledgeChat" /Users/pipi/Downloads/hermes-agent-main /Users/pipi/hermes-custom
```

Expected:

- Identify the route/component for `/knowledge-chat`.
- Identify the route/component for `/capture-review`.
- Identify the sidebar/navigation config.
- Identify API calls used by both pages.

- [ ] **Step 2: Write a route/navigation expectation test**

Add or update a frontend test so primary knowledge navigation exposes:

- `Knowledge Workbench`
- `Knowledge Cards`
- `System Logs` or existing logs label

Expected assertions:

- `Capture Review` is not shown as a separate primary navigation item after merge.
- The route can still be accessible as a redirect or secondary admin route during transition if needed.

Run the relevant frontend test command found in the repo. If no test harness exists, document the absence and use the Playwright/browser smoke test in Step 8 as the verification path.

- [ ] **Step 3: Define a shared Workbench state model**

The Workbench must represent these states without separate pages:

```ts
type WorkbenchMode =
  | "chat"
  | "draft_review"
  | "source"
  | "result";

type WorkbenchSelection =
  | { type: "conversation"; sessionId: string }
  | { type: "draft"; draftId: string }
  | { type: "material"; captureId: string }
  | { type: "card"; cardId: string };
```

If the frontend is not TypeScript, use the same state names in plain JavaScript.

- [ ] **Step 4: Move Capture Review list into the Workbench left rail**

The left rail should contain:

- current conversations;
- pending drafts;
- recent materials;
- recent commits;
- recent cards.

Reuse the existing Capture Review draft list instead of rebuilding it.

Each pending draft item should show:

- readable title;
- draft state;
- material type;
- short preview;
- last updated time if available.

Do not show naked draft IDs in user-facing labels.

- [ ] **Step 5: Move Capture Review detail into Workbench center**

Selecting a draft should switch `WorkbenchMode` to `draft_review` and show:

- title and user instruction;
- tabs: `整理稿`, `原文`, `元信息`;
- actions: `确认入库`, `继续修改`, `转知识卡片`;
- bottom input configured for revision instructions.

Reuse existing `show_capture_draft`, `revise_capture_draft`, and `commit_capture_draft` flows. Do not create a second draft mutation path.

- [ ] **Step 6: Keep Chat Mode as the default Workbench mode**

The default empty state should explain that the user can:

- ask the knowledge base;
- upload or paste material;
- review generated drafts;
- inspect tool calls and sources.

If a chat response creates a draft artifact, the Workbench should either:

- switch directly to `draft_review`, or
- show a clear "打开草稿" action that selects the draft.

Prefer direct switching when the draft is created in the current interaction.

- [ ] **Step 7: Add the right inspector**

The right inspector should have three tabs:

```text
来源
工具
状态
```

`来源` depends on:

- RAG citation outputs;
- Capture/Garden/Theme artifact refs;
- readable source titles and snippets.

`工具` depends on:

- Hermes result envelope tool metadata;
- run ledger or agent run events where available;
- generated artifact refs and next actions.

`状态` depends on:

- draft status;
- capture commit state;
- Library maintenance state;
- embedding/index state;
- Theme scan state;
- Garden sync state.

First implementation may show only fields already available in envelopes and current APIs. Missing backend fields should be listed in the implementation notes instead of faked.

- [ ] **Step 8: Browser smoke test**

Start or reuse the Hermes frontend, then open:

```text
http://127.0.0.1:9119/knowledge-chat
```

Verify:

1. Sidebar primary knowledge entry is `Knowledge Workbench` or equivalent.
2. No separate primary `Capture Review` entry is shown.
3. Empty Workbench shows chat input and workbench affordances.
4. Pending drafts appear in the left rail.
5. Selecting a draft shows the review panel in the center.
6. `整理稿 / 原文 / 元信息` tabs work.
7. `确认入库 / 继续修改 / 转知识卡片` actions remain visible.
8. Right inspector shows sources/tools/status placeholders or real data without raw internal IDs.

- [ ] **Step 9: Verify no duplicate backend logic was created**

Run:

```bash
rg -n "commit_capture|revise_capture|show_capture_draft|capture_draft" /Users/pipi/Downloads/hermes-agent-main /Users/pipi/hermes-custom
```

Expected:

- Workbench reuses existing draft APIs/tools.
- Old Capture Review components may remain as shared components, but there is no second independent commit/revise implementation.

---

### Task 7: Update Product Spec To Match The New Decision

**Files:**
- Modify: `/Users/pipi/hermes-custom/docs/superpowers/specs/2026-06-14-hermes-knowledge-product-redesign.md`

- [ ] **Step 1: Replace the old Theme section**

Change the old "Theme Agent weak/internal" wording to:

- Theme is a visible Obsidian-native structure layer, but not a separate frontend page.
- Theme automatically maintains `themes/*.md`.
- Theme pages are MOCs / relationship maps.
- Theme can suggest Garden Card directions.
- Garden Cards may come from Theme directions but Theme does not become a Garden Card container.

- [ ] **Step 2: Update implementation phase wording**

In P0/P2/P3, ensure the plan says:

- do not hide Theme from Obsidian;
- no Canvas revival in this change;
- no standalone Theme frontend page in this change;
- Cards Board may show `source_theme` as provenance.
- no standalone Capture Review primary page after Workbench merge;
- Knowledge Chat should be renamed or repositioned as Knowledge Workbench;
- Capture Review should be described as Draft Review Mode inside Workbench.

- [ ] **Step 3: Run a doc consistency check**

```bash
rg -n "Theme 降级|不主动推荐用户去生成主题页|Theme/MOC|Canvas" /Users/pipi/hermes-custom/docs/superpowers/specs/2026-06-14-hermes-knowledge-product-redesign.md
```

Expected:

- No remaining statement says Theme is merely deprecated or should not generate theme pages.
- Canvas can still be described as out of the mainline.

---

### Task 8: Migration And Smoke Test

**Files:**
- No new source file required unless a migration helper already exists and should be reused.
- Possible script if needed: `/Users/pipi/hermes-custom/scripts/repair_data.py`

- [ ] **Step 1: Back up DB and vault theme files**

Run:

```bash
mkdir -p /Users/pipi/hermes-custom/data/backups/theme-redesign
cp /Users/pipi/hermes-custom/data/captures.db /Users/pipi/hermes-custom/data/backups/theme-redesign/captures-$(date +%Y%m%dT%H%M%S).db
tar -czf /Users/pipi/hermes-custom/data/backups/theme-redesign/themes-$(date +%Y%m%dT%H%M%S).tgz -C /Users/pipi/Obsidian/hermes-vault themes
```

Expected: backup files are created.

- [ ] **Step 2: Dry-run inspect existing Theme pages**

Run a small inspection command:

```bash
find /Users/pipi/Obsidian/hermes-vault/themes -name '*.md' -maxdepth 1 -print
```

Expected: list current Theme pages before migration.

- [ ] **Step 3: Migrate only when writer tests pass**

For existing Theme pages:

- preserve current body outside Hermes managed block;
- create `## 我的补充` if missing;
- convert old generated MOC content into the new managed block when possible;
- do not delete existing Theme files automatically.

- [ ] **Step 4: Run full hermes-custom tests**

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/ -q
```

Expected: PASS.

- [ ] **Step 5: Restart Hermes with session hygiene**

Use the existing Hermes restart hygiene workflow:

- back up or clear `/Users/pipi/.hermes/sessions`;
- restart Hermes gateway;
- verify gateway logs show connected platform state.

- [ ] **Step 6: Manual smoke test**

From WeChat or Knowledge Chat:

1. Ask: `扫描最近保存的内容，看看有没有可以整理成主题页的关联`
2. Expected: Theme scan runs and reports candidate groups.
3. Open Obsidian `themes/`.
4. Expected: a readable Theme page exists or was updated.
5. Reply with a candidate number.
6. Expected: Garden draft is generated from `question_id`, not ad hoc RAG.
7. Confirm card.
8. Expected: `garden/*.md` is written and the source Theme page's `已沉淀的知识卡片` section updates once.

---

## 5. Acceptance Criteria

- Theme Agent automatically creates or updates `themes/*.md`.
- Theme pages are readable MOC-style relationship maps.
- Theme pages keep `related_garden_cards` and `已沉淀的知识卡片`.
- Theme pages preserve user-written `## 我的补充`.
- Garden Cards can originate from Theme candidates through `question_id`.
- Garden commit syncs card provenance back to the source Theme page.
- No naked internal IDs appear in Theme or Garden user-facing Markdown.
- The frontend does not add a separate Theme page.
- Cards Board may show source Theme as provenance.
- Existing Theme scan and candidate tools are reused instead of rebuilt.
- Knowledge Chat and Capture Review are merged into one Knowledge Workbench.
- Draft review is available as a Workbench mode with pending draft list, refined draft, raw/source view, metadata, revision input, and commit actions.
- The primary sidebar no longer presents Capture Review as a competing top-level knowledge workflow.
- The right inspector shows source, tool, and status information without requiring users to read raw logs.
- Workbench reuses existing Capture/Reading/RAG/Garden/Theme backend tools and does not create duplicate draft commit or revision logic.

## 6. Non-Goals

- Do not rebuild Theme Agent from scratch.
- Do not reintroduce Canvas as a primary product surface.
- Do not create a dedicated frontend Theme management page.
- Do not keep Capture Review as a separate primary knowledge workflow after Workbench merge.
- Do not force every Capture to become a Garden Card.
- Do not overwrite manually edited Theme sections outside the Hermes managed block.
- Do not remove existing Capture wiki-link behavior unless a later test proves it creates harmful noise.
- Do not create a new frontend-only draft state that can diverge from the Capture draft DB.

## 7. Self-Review Notes

- This plan resolves the conflict in the older 2026-06-14 redesign spec where Theme was described as too hidden.
- The plan preserves the original Obsidian MOC value while making file writes safer.
- The plan keeps the old code path names temporarily for compatibility and introduces clearer semantic names.
- The plan gives each long-term object a clear role: Capture is material, Theme is relationship map, Garden is reusable knowledge card.
- The plan resolves the UI overlap between Knowledge Chat and Capture Review by making Draft Review a Workbench state, not a separate primary page.
