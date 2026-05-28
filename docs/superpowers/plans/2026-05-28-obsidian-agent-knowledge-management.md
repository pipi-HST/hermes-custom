# Obsidian Agent Knowledge Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign Hermes knowledge management so Obsidian is the primary human workspace, with agents responsible for clear knowledge objects instead of loosely connected workflow steps.

**Architecture:** The vault uses four durable object layers: raw evidence, processed captures, theme/MOC pages, and garden opinion cards. Obsidian Markdown and Properties are the human-readable knowledge source of truth; SQLite remains a routing, cache, embedding, draft, and scheduling layer. Agents work around these Obsidian objects: Capture creates clean captures, Librarian keeps the vault consistent, Theme manages MOCs, Garden discovers and refines opinions, Review prevents forgetting, and RAG answers from the layered knowledge base.

**Tech Stack:** Python 3.11, SQLite, PyYAML frontmatter, Obsidian Markdown, Obsidian Properties, wiki-links, optional Obsidian Bases/Canvas, existing Hermes tool bridge.

---

## Current Diagnosis

The current system already has the right foundation:

- Capture Agent uses a draft-first flow and writes only after user confirmation.
- Markdown files are already the primary content store.
- SQLite is already slimmed down to routes, embeddings, review state, and agent state.
- Theme, Garden, Review, and RAG agents exist as early implementations.
- A repair tool and watcher already cover some DB/vault consistency needs.

The main weakness is not missing code volume. The weakness is that Obsidian is still treated mostly as a file folder, not as the knowledge workspace. The system needs a stricter object model and clearer agent boundaries.

## Core Design Decisions

- `captures` are processed material notes. They may contain summaries, outlines, facts, cases, and concepts, but they are not the place for the user's accepted viewpoints.
- `garden` is the place for user-facing viewpoints, frameworks, judgments, and long-running questions.
- Obsidian Properties are the main "identity card" for every note. Agents should inspect Properties before reading full text.
- If Obsidian Properties and SQLite conflict, knowledge fields in Obsidian win; machine state in SQLite wins.
- Agents are responsible for Obsidian object types, not one giant mixed workflow.
- Full agent artifacts should be written to durable storage. WeChat receives short previews, not full long-form outputs.
- Sub-agents must return structured results to the Hermes gateway/main agent so the main agent does not reinterpret knowledge content.

## Target Knowledge Objects

### `raw` Evidence

Purpose: preserve source material for traceability.

Decision:
- `raw` is not a daily reading area.
- `raw` should be treated as evidence/archive.
- It should remain reachable from captures, but not dominate Obsidian browsing.

Recommended direction:
- Move toward `_archive/raw/` or keep `raw/` but exclude it from primary Obsidian views.
- Every raw note should link to its capture through frontmatter.

Standard properties:

```yaml
id: abc123
type: raw
capture_id: abc123
title: 原始标题
date: 2026-05-28
source: https://example.com
source_type: link
status: archived
```

### `captures` Processed Materials

Purpose: readable processed notes created from external input.

Decision:
- `captures` are processed material notes, not final opinion cards.
- They are the normal searchable reading layer.
- They should be clean enough for human reading and structured enough for later agents.

Standard properties:

```yaml
id: abc123
type: capture
status: processed
title: 中文短标题
aliases:
  - 原始标题
date: 2026-05-28
created: 2026-05-28T10:00:00
source: https://example.com
source_type: link
tags:
  - ai/engineering
summary: 一句话摘要
outline:
  - 背景与问题
  - 核心机制
raw_id: abc123
linked_themes: []
garden_cards: []
reviewable: false
importance: normal
word_count: 1200
```

Body requirements:
- Long captures should include a `## 目录纲要` section before the detailed body.
- If the processed body exceeds roughly 1200 Chinese characters, Capture Agent should generate an outline in the draft preview.
- If the processed body exceeds roughly 3000 Chinese characters, Capture Agent should split the body into readable sections so RAG can expand only the relevant section later.
- The outline should be useful for navigation, not just decorative headings.

### `themes` MOC Pages

Purpose: semi-automatic topic shelves.

Decision:
- Theme pages are semi-automatic MOCs.
- Agents may create them cautiously, then the user may edit them.
- Theme Agent maintains links and incremental additions.

Rules:
- Three or more clearly related captures may create a new MOC automatically.
- Two related captures should receive links or a suggestion, not a new MOC by default.
- Uncertain theme names should be proposed to the user before creation.

Standard properties:

```yaml
id: theme-ai-engineering
type: theme
status: active
theme_name: AI 工程化
tags:
  - ai/engineering
captures: 4
open_questions:
  - AI 工程化的瓶颈为什么经常转移到组织能力？
last_scanned: 2026-05-28T10:00:00
garden_cards: []
```

### `garden` Opinion Cards

Purpose: evolving user-facing viewpoints, frameworks, and questions.

Decision:
- Garden cards are not summaries.
- They are living opinion objects created from multiple materials or a theme.
- The default state is `seed`, not final truth.
- They should support multi-turn refinement and user-triggered creation.

Good candidates:
- A reusable judgment.
- A cross-material framework.
- A long-term question.
- A counterintuitive finding.
- A user opinion draft that can be edited and reused.

Bad candidates:
- A single-article summary.
- A direct excerpt collection.
- A weak tag cluster with no argument.
- An agent-generated claim the user has not reviewed.

Standard properties:

```yaml
id: garden-ai-infra-bottleneck
type: garden
status: seed
source_captures:
  - abc123
source_themes:
  - theme-ai-engineering
claims:
  - AI 工程化的瓶颈经常从模型能力转移到组织和基础设施承载能力。
created: 2026-05-28T10:00:00
updated: 2026-05-28T10:00:00
```

Status rules:
- `seed`: the user allowed the card into `garden`, but the viewpoint is still being shaped.
- `accepted`: the user explicitly accepts the viewpoint. RAG may describe this as the user's formed view.
- `archived`: the card is no longer active, was rejected, or is kept only for history.

Do not add extra maturity or confidence state until there is a demonstrated need. Nuance should live in the card body.

Body template:

```markdown
# 观点标题

## 一句话

## 核心观点

## 为什么这些材料有关

## 支撑材料

## 反例与边界

## 我的修改

## 待继续想
```

## Agent Responsibilities

### Capture Agent

Role: information intake and clean capture creation.

Responsibilities:
- Accept links, text, images, and voice transcripts from Hermes/WeChat.
- Extract and clean content.
- Save draft first and wait for user confirmation by default.
- Commit confirmed drafts into raw evidence and processed capture notes.
- Generate readable Chinese-first filenames.
- Add standard properties to raw and capture notes.
- Generate an outline for long captures before commit.
- Perform lightweight duplicate checks before commit.

Non-responsibilities:
- Do not decide themes.
- Do not create garden cards.
- Do not schedule long-term review.
- Do not perform vault-wide cleanup.

### Librarian Agent

Role: vault and DB consistency manager.

Responsibilities:
- Keep SQLite and Obsidian paths aligned.
- Detect manually deleted files and safely sync DB.
- Normalize filenames and required properties.
- Detect missing raw/capture links.
- Report duplicate captures, duplicate drafts, missing metadata, broken links, and orphan notes.
- Automatically apply safe mechanical fixes.
- Ask before knowledge-level changes such as merging, deleting non-identical duplicates, or changing theme assignment.

Modes:
- Automatic: safe mechanical cleanup.
- Advisory: suggestions that require human judgment.

### Theme Agent

Role: topic shelf and MOC manager.

Responsibilities:
- Discover related captures.
- Add bidirectional wiki-links between clearly related captures.
- Create MOCs only when at least three captures are clearly related.
- Update existing MOCs with new related captures.
- Maintain open questions and recent additions.
- Suggest uncertain themes instead of creating them.

Non-responsibilities:
- Do not create final viewpoints.
- Do not clean raw/capture data.
- Do not run review scheduling.

### Garden Agent

Role: viewpoint discovery and multi-turn refinement assistant.

Responsibilities:
- Find unexpected links and possible viewpoints from existing captures/themes.
- Ask before creating a seed garden card.
- Support user-triggered card creation from a topic, theme, or selected captures.
- Support multi-turn edits to the same card.
- Support weakening a claim, adding counterexamples, changing the angle, splitting, merging, and changing card status to `accepted` or `archived`.
- Write garden cards as Obsidian Markdown, not primarily as frontend data.

Non-responsibilities:
- Do not mass-generate final cards.
- Do not treat summaries as garden cards.
- Do not mark cards as accepted without user confirmation.

### Review Agent

Role: forgetting prevention coach and important-knowledge recall manager.

Primary responsibility:
- Bring important knowledge points back at the right time so the user does not forget them.

Secondary responsibility:
- Help move important knowledge from capture to theme to garden over time.

Review objects:
- Important captures: knowledge points, facts, concepts, cases, and arguments worth remembering.
- Themes: relationships and mechanisms across multiple captures.
- Garden cards: user viewpoints and reusable frameworks.

Review fields:

```yaml
review_state: new | learning | familiar | mastered
last_reviewed:
next_review:
review_interval:
```

Who sets review fields:
- Capture Agent may set `reviewable` and an initial `importance` suggestion when the generated capture clearly contains durable knowledge.
- Theme Agent may suggest reviewable mechanisms when a theme/MOC exposes recurring relationships across captures.
- Garden Agent may mark accepted garden cards as reviewable because those are viewpoint objects.
- Review Agent owns the schedule fields: `review_state`, `last_reviewed`, `next_review`, and `review_interval`.
- Review Agent should generate concrete recall points from the note's Properties, outline, body sections, and linked context. It should not force an entire article into one review type.
- The user has final authority. If the user says "这个不值得复习", "这个很重要", or "这个我已经掌握", the agent should update the fields accordingly.

Review state rules:
- `new`: created but never reviewed.
- `learning`: reviewed and not yet stable, or user answered poorly.
- `familiar`: user recalled the point with partial or full success.
- `mastered`: user repeatedly recalled the point or explicitly marked it mastered.

Interaction pattern:
- Ask a small recall question.
- Let the user answer.
- Give feedback with the correct point and source.
- Update review state.
- Record a short review trace in a HERMES-managed Obsidian block, while SQLite owns scheduling.

Example:

```text
小回顾：为什么巴基斯坦土地记录数字化改革反而削弱了税收征管能力？
```

Non-responsibilities:
- Do not randomly bombard the user with low-value captures.
- Do not rewrite opinions without confirmation.
- Do not force daily pushes when no useful review item exists.

### RAG/Search Agent

Role: single-entry knowledge-base Q&A with broad but bounded retrieval.

Decision:
- Do not expose multiple user-facing modes.
- The user should ask normal questions.
- The agent should behave like a normal RAG answerer, but search more broadly by using the vault's existing garden/theme/capture structure.

Responsibilities:
- Answer based on the knowledge base.
- Search garden first, then theme, then capture as the primary entry path.
- When a higher-level object is found, expand along its related network instead of stopping there.
- Cite sources.
- Provide a concise source map by default.
- Distinguish accepted viewpoints from raw materials.
- Say when the knowledge base has relevant materials but no formed viewpoint.
- Say when the knowledge base lacks sufficient evidence.
- Suggest other agents when useful, such as Garden Agent for repeated questions without a viewpoint.

Retrieval path:
- Step 1: Search `garden` for a directly relevant viewpoint card.
- Step 2: If garden hits exist, expand from those cards to their source themes and source captures.
- Step 3: If no garden hit exists, search `themes` for a relevant MOC.
- Step 4: If theme hits exist, expand from those MOCs to linked captures and related garden cards.
- Step 5: If no theme hit exists, search `captures` directly.
- Step 6: If capture hits exist, expand through the strongest wiki-links, linked themes, and related garden cards until the answerability check passes or the time budget is reached.
- Step 7: Use `raw` only when checking source text or when the capture is missing enough detail.

Answer assembly:
- Garden and Theme are navigation and organization layers.
- Capture is the primary evidence layer.
- Raw is a source verification layer.
- Garden, when present and `accepted`, may provide the user's viewpoint or framing.
- Theme, when present, gives structure and topic boundaries.
- Capture gives the details, cases, facts, and support for a longer answer.
- Long captures should be read through Properties, outline, and section-level expansion instead of full-body stuffing.

Source map:
- Every important answer should include compact source attribution.
- Distinguish viewpoint source, structure source, and evidence source.
- Example:

```text
参考来源：
- Garden：《AI 工程化瓶颈》accepted
- Theme：《AI 工程化》
- Capture：《AI Harness 深度解析》/ 工具调用与评估
```

Quality-adaptive retrieval controls:
- Search should optimize for answer quality first, then stop as soon as extra searching is unlikely to improve the answer.
- Use staged retrieval rather than fixed per-layer counts.
- Stage 1 is fast lexical/property search over titles, aliases, tags, summaries, claims, theme names, and open questions.
- Stage 2 is semantic search over the most relevant object layer from the garden/theme/capture entry path.
- Stage 3 expands the strongest hits through their Obsidian network.
- Stage 4 adds more captures only if the current evidence is too thin, one-sided, or missing concrete examples.

Answerability check:
- Before expanding further, score whether the current material can answer the question.
- A good answer usually needs a relevant main thread, enough supporting evidence, and clear sources.
- For a simple factual question, one or two strong sources may be enough.
- For an explanatory or opinion question, the answer should usually have a framing object plus multiple capture-level supports.
- If the question asks for evidence, captures matter more than garden cards.
- If the question asks "我怎么看", garden/theme hits matter more, but captures are still needed for detail.

Expansion stop rules:
- Stop when the current material covers the question and new hits are mostly redundant.
- Continue when a new hit adds a missing angle, counterexample, concrete case, or source trail.
- Prefer one strong network path over broad full-vault scanning.
- Deduplicate notes by `id`, `file_path`, and source URL.
- If the normal interaction time budget is nearly exhausted, answer with the best material found and state the limit plainly.
- A deeper search can be triggered only when the user asks for it, such as "多找一点", "查深一点", or "多给证据".

Non-responsibilities:
- Do not modify the vault by default.
- Do not create themes or garden cards directly.
- Do not expose a menu of modes unless the user naturally asks for a constraint, such as "only show sources" or "do not synthesize."

## Hermes Gateway Output Contract

WeChat has practical message length limits, and the Hermes gateway/main agent may rephrase tool results. Agent design must separate durable work from chat display.

Principles:
- Full artifacts go to durable storage: Obsidian Markdown, SQLite draft rows, reports, or logs.
- WeChat receives a short `chat_preview` suitable for the channel.
- Tool and bridge responses should return structured data, not only a natural-language paragraph.
- The gateway/main agent may route and lightly polish wording, but should not reinterpret knowledge content, alter citations, or upgrade a `seed` garden card into an accepted viewpoint.

All bridge and tool responses should use an `Agent Result Envelope`.

```json
{
  "status": "success",
  "agent": "capture",
  "intent": "create_capture_draft",
  "chat_preview": "已生成草稿《AI Harness 深度解析》。回复「存库」确认，或继续说明怎么改。",
  "durable_artifacts": [
    {
      "type": "capture_draft",
      "id": "draft-abc123",
      "title": "AI Harness 深度解析",
      "path": "",
      "status": "draft_ready"
    }
  ],
  "source_map": [],
  "next_actions": [
    {
      "label": "存库",
      "intent": "commit_capture_draft",
      "payload": {"draft_id": "draft-abc123"},
      "requires_confirmation": true
    },
    {
      "label": "继续修改",
      "intent": "revise_capture_draft",
      "payload": {"draft_id": "draft-abc123"},
      "requires_confirmation": false
    }
  ],
  "warnings": [],
  "errors": [],
  "meta": {
    "preview_truncated": false
  }
}
```

Required envelope fields:
- `status`: `success`, `warning`, or `error`.
- `agent`: `capture`, `librarian`, `theme`, `garden`, `review`, or `rag`.
- `intent`: the machine-readable action that was performed.
- `chat_preview`: the short channel-friendly message. The gateway may display or lightly polish this, but should not reinterpret knowledge content.
- `durable_artifacts`: list of durable artifact references.
- `source_map`: list of structured source references.
- `next_actions`: list of structured follow-up actions.
- `warnings`: non-fatal issues or degraded behavior.
- `errors`: fatal issues when `status` is `error`.
- `meta`: diagnostics such as timing, truncation, or compatibility fields. It is not the primary user-facing surface.

Fields may be empty, but they should remain present so the Hermes gateway/main agent does not need agent-specific parsing.

Durable artifact reference shape:

```json
{
  "type": "capture_note",
  "id": "abc123",
  "title": "AI Harness 深度解析",
  "path": "captures/AI Harness深度解析-a69f3367.md",
  "status": "created"
}
```

Durable artifact reference fields:
- `type`: `capture_draft`, `raw_evidence`, `capture_note`, `theme_page`, `garden_card`, `librarian_report`, or `review_trace`.
- `id`: durable object ID when available.
- `title`: human-readable title.
- `path`: vault/report path when available. Use an empty string when the artifact is DB-only.
- `status`: artifact state, such as `draft_ready`, `created`, `seed`, `accepted`, `archived`, or `reported`.

Source reference shape:

```json
{
  "role": "evidence",
  "type": "capture_section",
  "id": "abc123",
  "title": "AI Harness 深度解析",
  "path": "captures/AI Harness深度解析-a69f3367.md",
  "section": "工具调用与评估",
  "status": ""
}
```

Source reference fields:
- `role`: `viewpoint`, `structure`, `evidence`, or `verification`.
- `type`: `garden_card`, `theme_page`, `capture_note`, `capture_section`, or `raw_evidence`.
- `id`: source object ID when available.
- `title`: human-readable source title.
- `path`: vault path when available.
- `section`: section name for `capture_section`; otherwise empty.
- `status`: relevant object state, such as `seed`, `accepted`, or `archived` for garden cards.

Next action shape:

```json
{
  "label": "存库",
  "intent": "commit_capture_draft",
  "payload": {"draft_id": "draft-abc123"},
  "requires_confirmation": true
}
```

Next action rules:
- `label` is for the user.
- `intent` and `payload` are for gateway/tool routing.
- Write, delete, repair, create-object, or state-changing actions must use `requires_confirmation: true`.
- Read-only expansion actions, such as showing a full draft, expanding a capture section, showing a source map, or dry-run audit, may use `requires_confirmation: false`.

Channel-specific guidance:
- Capture draft previews should show title, summary, outline, draft ID, and next actions. Full draft text stays in SQLite until commit.
- RAG answers should stay concise in WeChat and include a compact source map. Longer answers should be saved or expanded on request.
- Review prompts should ask one small question at a time.
- Theme, Garden, and Librarian reports should summarize the result in WeChat and point to the durable artifact/report when detailed output is long.

## Agent Trigger Model

Agent triggers should stay simple and explicit. The system should support three trigger classes:

1. User-triggered: the user directly asks for an action in WeChat/Hermes or a local command.
2. Event-triggered: a previous agent or file event creates a specific next step.
3. Scheduled: a cron/LaunchAgent/heartbeat-style job runs a limited maintenance or review pass.

### Capture Agent Triggers

User-triggered:
- User sends a link, text, image, or voice transcript.
- User says "整理这个", "总结这篇", "提取重点", or similar.
- User says "存库", "可以", "就这样", or similar to commit the latest draft.

Event-triggered:
- `capture_content` creates a draft and waits for revision or commit.
- `revise_capture_draft` updates an existing draft and waits for commit.
- `commit_capture_draft` writes raw/capture notes and may enqueue Librarian validation.
- Confirmed commits leave the capture eligible for later Theme scanning; Capture Agent should not run Theme work inline.

Scheduled:
- None by default. Capture Agent should not autonomously ingest content.

### Librarian Agent Triggers

User-triggered:
- User asks to audit, sync, clean, rename, or repair the vault.
- User manually deletes files and wants immediate DB alignment.

Event-triggered:
- Capture Agent commits a new capture.
- Theme Agent updates links or MOCs.
- Garden Agent creates or edits a garden card.
- Vault watcher detects file changes.

Scheduled:
- Run a safe audit periodically, such as every 6 hours.
- Run safe mechanical repair only for approved classes of changes.
- Generate a weekly maintenance report.

### Theme Agent Triggers

User-triggered:
- User asks to find related notes, build a theme, update a MOC, or inspect a topic.

Event-triggered:
- A new capture with embedding is committed and remains `theme_scanned_at IS NULL`.
- Librarian flags multiple orphan captures with overlapping tags or semantic similarity.

Scheduled:
- Scan unscanned captures in batches, such as every 6 hours.
- Create MOCs only when the threshold and clear-evidence rules are met.
- Otherwise produce suggestions.
- Do not trigger on every draft, raw file change, or Obsidian save.

### Garden Agent Triggers

User-triggered:
- User asks to make a viewpoint card from a topic, theme, or selected captures.
- User asks to revise, weaken, strengthen, split, merge, or accept a garden card.

Event-triggered:
- Theme Agent finds a theme with enough material and proposes a garden candidate.
- RAG/Search notices repeated questions with no existing viewpoint card.
- Review Agent asks whether a recurring review item should become a garden seed.

Scheduled:
- Optional candidate discovery, but it should only propose candidates.
- It should not mass-generate garden cards on a schedule.

### Review Agent Triggers

User-triggered:
- User asks for a quiz, review, daily review, weekly review, or review of a specific theme/card.
- User answers a recall question, which triggers feedback and schedule update.

Event-triggered:
- A capture/theme/garden object is created with reviewable content.
- User marks an item important.
- Garden Agent creates an accepted card or the user marks a seed as worth reviewing.

Scheduled:
- Daily limited review queue for due items.
- Weekly knowledge review summary.
- Proactive pushes should be capped and skipped if no high-value review item exists.

### RAG/Search Agent Triggers

User-triggered:
- User asks any natural-language question about their knowledge base.
- User asks what they know, what evidence exists, or what their view is.

Event-triggered:
- RAG/Search may suggest Theme or Garden follow-up when a query reveals a gap.
- It should not write to the vault by default.

Scheduled:
- None by default. Search should not proactively run unless part of another agent's scheduled job.

## Implementation Roadmap

This roadmap is intentionally phased. Each phase should produce useful behavior on its own.

### Phase 0: Stabilize Real Data Before Feature Work

Goal: make the current vault and DB trustworthy before changing semantics.

- [ ] Run `scripts/repair_data.py --rename-readable` and inspect dry-run output.
- [ ] Decide whether to apply deletion sync for missing captures already removed from Obsidian.
- [ ] Run `scripts/repair_data.py --apply --rename-readable` only after confirming the dry-run report.
- [ ] Verify `captures` DB count equals `captures/*.md` file count.
- [ ] Verify every DB `file_path` exists.
- [ ] Verify every existing raw/capture pair is connected by ID.

Success criteria:
- Dry-run reports zero planned actions after apply.
- Existing notes remain readable in Obsidian.
- No backup or repair report is committed.

### Phase 1: Capture Object Model Cleanup

Goal: make new captures produce the right Obsidian objects.

Files likely involved:
- `agents/markdown_writer.py`
- `agents/capture_agent.py`
- `bridge/capture_bridge.py`
- `tests/test_markdown_writer.py`
- `tests/test_capture_draft_workflow.py`

Tasks:
- [ ] Update raw frontmatter to include `type: raw`, `capture_id`, and `status: archived`.
- [ ] Update capture frontmatter to include `type: capture`, `status: processed`, `raw_id`, `aliases`, `linked_themes`, `garden_cards`, `summary`, `outline`, `reviewable`, and `importance`.
- [ ] Keep title-first Chinese-readable filenames.
- [ ] Add long-capture body structure with `## 目录纲要` and section headings.
- [ ] Keep WeChat draft preview short while storing the full draft in SQLite.
- [ ] Add tests for raw/capture frontmatter.
- [ ] Add tests for long-capture outline generation or preservation.
- [ ] Add a lightweight duplicate-source warning before commit.
- [ ] Keep the draft-first confirmation flow unchanged.

Success criteria:
- New confirmed captures create one raw evidence note and one processed capture note.
- Capture files are suitable for Obsidian Properties/Bases.
- Long captures can be navigated by outline before reading full text.
- Capture Agent still does not create themes or garden cards.

### Phase 2: Librarian Agent MVP

Goal: separate vault maintenance from content agents.

Files likely involved:
- Create `agents/librarian_agent.py`
- Extend or wrap `scripts/repair_data.py`
- Create `bridge/librarian_bridge.py`
- Add tests under `tests/test_librarian_agent.py`

Tasks:
- [ ] Extract reusable audit logic from `repair_data.py` without changing behavior.
- [ ] Add a Librarian report object with sections for DB sync, missing files, duplicate candidates, property gaps, orphan captures, and raw/capture link gaps.
- [ ] Allow automatic application only for safe mechanical fixes.
- [ ] Return advisory suggestions for merge/delete/theme/garden decisions.
- [ ] Add a Hermes tool only after the local script behavior is tested.

Success criteria:
- Librarian can explain what is wrong with the vault in human language.
- Librarian can safely fix mechanical issues.
- Librarian never performs knowledge judgment without confirmation.

### Phase 3: Theme Agent MOC Tightening

Goal: make themes useful as Obsidian topic shelves.

Files likely involved:
- `agents/theme_agent.py`
- `bridge/theme_bridge.py`
- `agents/markdown_writer.py`
- `tests/test_theme_agent.py`
- `tests/test_theme_bridge.py`

Tasks:
- [ ] Add standard theme properties to MOC creation.
- [ ] Keep auto-create threshold at three clearly related captures.
- [ ] For two related captures, add links or produce an advisory suggestion.
- [ ] Preserve user edits in existing MOCs by updating only HERMES-managed blocks.
- [ ] Add `open_questions` and `last_scanned` fields.
- [ ] Document and test the 6-hour batch trigger separately from user-triggered immediate scans.

Success criteria:
- Theme pages are useful in Obsidian.
- Theme creation is conservative.
- Existing user-edited MOCs are not overwritten.

### Phase 4: Garden Agent Reframing

Goal: turn Garden Agent into a viewpoint discovery and refinement assistant.

Files likely involved:
- `agents/garden_agent.py`
- `bridge/garden_bridge.py`
- `db/schema.py`
- `tests/test_garden_agent.py`
- `tests/test_garden_bridge.py`

Tasks:
- [ ] Introduce only three statuses: `seed`, `accepted`, and `archived`.
- [ ] Generate seed cards only after user confirmation or direct user request.
- [ ] Add support for revising an existing garden card.
- [ ] Add support for adding counterexamples and boundaries.
- [ ] Add support for split and merge proposals.
- [ ] Allow user confirmation to change `seed` to `accepted`.
- [ ] Store the main card as Obsidian Markdown.
- [ ] Treat DB rows as routing/state, not as the primary garden UI.

Success criteria:
- Garden Agent supports multi-turn refinement.
- Garden cards are living Obsidian notes.
- No automatic mass generation of garden cards occurs.

### Phase 5: Review Agent Memory Loop

Goal: make Review Agent prevent forgetting without becoming noisy.

Files likely involved:
- `agents/review_agent.py`
- `bridge/review_bridge.py`
- `db/schema.py`
- `tests/test_review_agent.py`

Tasks:
- [ ] Add review fields for `reviewable`, `importance`, `review_state`, `last_reviewed`, `next_review`, and `review_interval`.
- [ ] Select review items by importance, due date, review state, and object type.
- [ ] Generate review questions from knowledge points, not whole articles.
- [ ] Use capture outlines and section headings to locate reviewable points in long notes.
- [ ] Prefer useful recall questions over passive summaries.
- [ ] Support user answer feedback.
- [ ] Limit proactive daily pushes to a small number.
- [ ] Write short review traces to HERMES-managed Obsidian blocks while keeping scheduling in SQLite.
- [ ] Add weekly knowledge review summary.

Success criteria:
- Review Agent asks memory-relevant questions.
- User can answer and receive feedback.
- Low-value captures do not flood daily review.

### Phase 6: RAG/Search Layered Answering

Goal: make RAG answer naturally from the layered knowledge base.

Files likely involved:
- `agents/rag_agent.py`
- `bridge/rag_bridge.py`
- `db/vector_search.py`
- `tests/test_rag_agent.py`

Tasks:
- [ ] Retrieve across garden, theme, and capture layers.
- [ ] Prefer garden/theme when answering opinion-style questions.
- [ ] Fall back to captures when no higher-level object exists.
- [ ] Treat garden/theme as navigation layers and capture as the evidence layer.
- [ ] For long captures, read Properties and outline first, then expand only relevant sections.
- [ ] Use raw only for source traceability.
- [ ] Make the answer say whether it is based on accepted viewpoints or only materials.
- [ ] Include a compact source map in default answers.
- [ ] Add quality-gap and time-budget stopping logic instead of fixed per-layer counts.
- [ ] Keep one natural-language entry point.
- [ ] Do not expose explicit modes unless the user requests a constraint.

Success criteria:
- User asks normal questions.
- Answers cite sources.
- Answers show Garden/Theme/Capture sources when available.
- The agent distinguishes "your formed view" from "related material exists."

### Phase 7: Hermes Output Contract Hardening

Goal: make every agent safe to use through WeChat and the Hermes gateway/main agent.

Migration strategy:
- Use a one-time migration to the `Agent Result Envelope`.
- Do not maintain a long-term dual response contract with legacy top-level fields such as `message`, `formatted`, `draft_id`, `card_id`, or plain string results.
- Update bridge functions, Hermes tool handlers, and tests in the same phase so the gateway receives only the new envelope shape after the migration.
- Legacy fields may be used temporarily while editing tests and handlers, but they should not remain part of the accepted contract.

Migration route:

| Order | Slice | Why first/next | Verification |
| --- | --- | --- | --- |
| 1 | Envelope helper and contract tests | Establish the shared shape before touching agent behavior. | `tests/test_result_envelope.py` passes and rejects missing top-level fields. |
| 2 | Capture bridge draft path | Capture is the system entry point and already has the strongest draft-preview constraints. | `capture_content` returns a Capture Draft envelope with short `chat_preview`, durable draft reference, and confirmable `commit_capture_draft` next action. |
| 3 | Capture revise and commit path | Completes the core intake pipeline from draft to durable notes. | `revise_capture_draft` returns an envelope; `commit_capture_draft` writes Raw Evidence and Capture Note, and artifact paths resolve in a temp vault. |
| 4 | RAG bridge and source map | RAG has the highest risk of citation loss and main-agent reinterpretation. | `search_knowledge_base` returns an envelope with structured `source_map` and no legacy `formatted` contract. |
| 5 | Review bridge | Review is narrow and validates the one-question chat-preview pattern. | `run_daily_review` returns an envelope with at most a short prompt and optional Review Trace reference. |
| 6 | Theme bridge | Theme scan can produce reports, Theme Pages, and next actions, so it should follow the simpler bridges. | `run_theme_scan` and `run_graph_report` return envelopes with report/page artifacts and no plain string primary result. |
| 7 | Garden bridge | Garden has state-sensitive output (`seed`, `accepted`, `archived`) and should migrate after source/action patterns are proven. | `synthesize_garden` returns a Garden Card envelope; creating or accepting cards requires confirmation where applicable. |
| 8 | Hermes tool handlers | Handlers should adapt to the new bridge contract after all bridges speak Envelope. | Tool handler tests display `chat_preview` and preserve envelope fields needed by the gateway. |
| 9 | Full mocked pipeline regression | Proves the local flow works without real gateway or external services. | Temp DB/temp vault test covers capture draft -> revise -> commit -> files -> artifact references. |
| 10 | Manual gateway/WeChat smoke test | Only after deterministic local tests pass, verify the real channel. | Fixed WeChat smoke-test message returns previews and writes matching Durable Artifacts. |

Do not move to the next slice until the current slice's verification passes. The migration is one-time, but the implementation should still be reviewed slice by slice.

Files likely involved:
- Create `bridge/result_envelope.py`
- `bridge/capture_bridge.py`
- `bridge/rag_bridge.py`
- `bridge/theme_bridge.py`
- `bridge/garden_bridge.py`
- `bridge/review_bridge.py`
- Hermes tool handlers in `/Users/pipi/Downloads/hermes-agent-main/tools/`
- `tests/test_e2e_hermes_tools.py`

Tasks:
- [x] Create `bridge/result_envelope.py` with helpers such as `success_envelope`, `error_envelope`, `artifact_ref`, `source_ref`, and `next_action`.
- [x] Define a shared `Agent Result Envelope` helper/schema with stable top-level fields.
- [x] Use ordinary `dict` helpers and tests for Phase 7; do not introduce Pydantic unless the contract becomes more complex later.
- [x] Convert bridge responses from plain strings or agent-specific dictionaries to envelopes.
- [x] Return durable artifact references instead of top-level agent-specific IDs such as `draft_id`, `card_id`, or `report_path`.
- [x] Return structured source references in `source_map` instead of untyped source strings.
- [x] Return structured next actions with `label`, `intent`, `payload`, and `requires_confirmation`.
- [x] Keep full artifacts in durable storage and return short WeChat `chat_preview` text.
- [x] Add handler-level guardrails so the main agent does not alter citations, source maps, garden status, or next-action confirmation requirements.
- [x] Add tests for envelope field stability, long capture preview truncation, RAG source map preservation, next-action confirmation, and tool-handler envelope display.
- [x] Remove or rewrite tests that assert legacy response fields as the primary contract.
- [x] Keep channel formatting separate from agent reasoning output.

Phase 7 implementation note, 2026-05-29:
- Local automated verification passed: `.venv/bin/python -m pytest tests/ -q` -> `202 passed, 2 skipped`.
- Hermes venv handler verification passed: `PYTHONPATH=/Users/pipi/hermes-custom /Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_e2e_hermes_tools.py -q -k 'hermes'` -> `29 passed, 2 skipped`.
- Spec compliance re-review used `code-reviewer` with `gpt-5.5` and `reasoning_effort=high`; the tool exception-envelope and chat-preview adapter blockers were approved as resolved.
- Code quality review used `code-reviewer` with `gpt-5.5` and `reasoning_effort=high`; no blocking issues were found in the external Hermes integration changes.
- Hermes gateway was restarted after the external tool-handler changes; logs showed Weixin and Feishu connected, `Gateway running with 2 platform(s)`, and `Cron ticker started`.
- Real WeChat message smoke testing remains manual.

Success criteria:
- WeChat responses remain readable within channel limits.
- Full knowledge artifacts are not lost because of chat truncation.
- The gateway/main agent can route results without changing knowledge meaning.
- All bridge/tool responses share the same envelope shape, even when some fields are empty.
- Legacy bridge/tool response shapes are no longer accepted after Phase 7.

Test plan:
- Envelope unit tests:
  - `success_envelope` and `error_envelope` always include `status`, `agent`, `intent`, `chat_preview`, `durable_artifacts`, `source_map`, `next_actions`, `warnings`, `errors`, and `meta`.
  - `artifact_ref` always returns `type`, `id`, `title`, `path`, and `status`.
  - `source_ref` always returns `role`, `type`, `id`, `title`, `path`, `section`, and `status`.
  - `next_action` always returns `label`, `intent`, `payload`, and `requires_confirmation`.
  - Write, delete, repair, create-object, or state-changing actions require confirmation in tests.
- Bridge contract tests:
  - `capture_bridge`, `rag_bridge`, `theme_bridge`, `garden_bridge`, and `review_bridge` all return Agent Result Envelopes.
  - Tests should fail if a bridge returns a plain string or a legacy agent-specific top-level shape as the primary contract.
  - Rewrite old assertions from `result["message"]` to `envelope["chat_preview"]`.
  - Rewrite old assertions from `result["draft_id"]`, `result["card_id"]`, or similar top-level IDs to `envelope["durable_artifacts"][...]["id"]`.
  - Rewrite old assertions from `isinstance(result, str)` to envelope status and `chat_preview` checks, except for final external framework adapters that truly require a string display.
- Local pipeline tests with temporary SQLite and temporary vault:
  - `capture_content` returns a Capture Draft envelope.
  - `revise_capture_draft` updates the draft and keeps full content out of `chat_preview` when long.
  - `commit_capture_draft` writes Raw Evidence and a Capture Note.
  - `durable_artifacts` IDs and paths resolve to the actual draft or Markdown files.
- RAG/source-map tests:
  - `search_knowledge_base` returns an envelope with a concise `chat_preview`.
  - `source_map` includes structured `viewpoint`, `structure`, and `evidence` references when those objects exist.
  - Long Capture Notes are cited by Capture Section when a section is used.
- Hermes tool handler integration tests:
  - Tool handlers display `chat_preview` and preserve the envelope fields needed by the gateway.
  - Handlers do not alter citations, source maps, garden status, or `requires_confirmation`.
  - Automated tests should stop at the tool-handler boundary and should not start the real Hermes gateway.
- Manual smoke tests:
  - Run mocked local tests by default.
  - Run `RUN_REAL_TESTS=1 .venv/bin/python -m pytest tests/test_e2e_hermes_tools.py -k real -q` only when checking real LLM, embedding, and external extraction paths.
  - Start the Hermes gateway and send a real WeChat message only as a manual smoke test, then verify the returned `chat_preview` and the corresponding Durable Artifact in SQLite or the vault.
  - Use this fixed WeChat smoke-test message to avoid external link/extraction dependencies:

```text
测试入库：知识管理有三个层次：捕获、整理、复习。捕获负责把原始信息变成草稿，整理负责把材料变成可读笔记，复习负责防止重要知识遗忘。请整理成一条测试 Capture Note。
```

  - Manual smoke-test acceptance:
    - WeChat returns a `chat_preview` with a draft title, summary, and a "存库" next action.
    - Replying "存库" returns a success `chat_preview`.
    - The vault contains matching Raw Evidence and Capture Note files.
    - SQLite contains the corresponding capture row.
    - The Durable Artifact reference path opens the expected Markdown file.

### Phase 8: Obsidian Workspace Enhancements

Goal: use Obsidian features without overbuilding a custom frontend.

Files likely involved:
- `_templates/` in the vault
- Optional `.canvas` files in the vault
- Optional Obsidian Bases configuration

Tasks:
- [ ] Add templates for capture, theme, garden, and daily review notes.
- [ ] Add optional daily review notes under `daily/`.
- [ ] Use Canvas for viewpoint/theme maps only after garden notes exist.
- [ ] Use Bases or Properties views for `captures`, `themes`, `garden seed`, and `review due`.
- [ ] Avoid building a Garden frontend until Obsidian workflows prove insufficient.

Success criteria:
- Obsidian becomes the primary human workspace.
- Dashboard remains optional and operational, not the main knowledge UI.

## Non-Goals

- Do not rebuild the whole system at once.
- Do not make raw notes the daily reading surface.
- Do not auto-generate garden cards at scale.
- Do not expose RAG modes as a menu.
- Do not let agents overwrite user-edited Obsidian notes without merge-safe behavior.
- Do not make the Hermes gateway/main agent reinterpret structured agent results.
- Do not commit real DB files, backups, repair reports, or private vault content.

## Risk Controls

- Keep every data-changing operation dry-run by default until the behavior is trusted.
- Back up SQLite before any apply operation.
- Prefer append/update sections over full-file rewrites for MOCs and garden cards.
- Add tests before changing agent behavior.
- Keep each phase small enough to verify independently.
- Preserve existing user edits in Markdown.
- Keep WeChat previews separate from durable artifacts.
- Preserve citations and status fields across bridge/tool/main-agent boundaries.

## Validation Checklist

- [ ] New captures are draft-first and confirmation-based.
- [ ] Raw notes are evidence/archive, not daily reading notes.
- [ ] Captures have standard Properties.
- [ ] Long captures have outlines and section structure.
- [ ] Librarian reports are understandable and safe.
- [ ] Theme pages are created conservatively.
- [ ] Garden cards require user confirmation or direct request.
- [ ] Review Agent asks recall questions that prevent forgetting.
- [ ] RAG answers normal questions without requiring mode selection.
- [ ] RAG answers include a compact source map.
- [ ] Agent outputs survive WeChat limits through structured short previews and durable artifacts.
- [ ] Obsidian remains the primary workspace for knowledge objects.

## Recommended First Implementation Slice

Start with Phase 1 plus the safe subset of Phase 2:

1. Standardize raw/capture Properties.
2. Preserve the current draft-first Capture flow.
3. Add long-capture outline structure.
4. Keep WeChat previews short while saving full artifacts.
5. Add Librarian audit reporting without broad automatic mutation.
6. Only after that, tighten Theme/Garden/Review/RAG.

This slice improves the foundation while avoiding speculative feature churn.
