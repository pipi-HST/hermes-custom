# Garden Knowledge Card Design

## Summary

Garden should not mean "opinion only". In Hermes, `garden/` should become the place for refined knowledge cards: small reusable knowledge objects extracted from captures and themes. A card can be a viewpoint, fact, method, caution, or question. The common standard is not "does it have an opinion?", but "is this useful enough to be reused independently from the original article?"

This keeps the current Obsidian-first architecture:

- `raw/` stores source evidence and should stay out of daily reading.
- `captures/` stores processed material notes.
- `themes/` stores topic maps and MOCs.
- `garden/` stores refined knowledge cards.

The existing Garden Agent should evolve into a Knowledge Card Agent. The directory can still be named `garden/`, but the object type should be broader than viewpoint cards.

## Problem

The current Garden design correctly avoids turning cards into summaries, but it is still too biased toward viewpoint generation. Not every useful note needs a claim. Some materials are valuable because they contain:

- a stable fact;
- a practical method;
- a risk or caveat;
- a question worth tracking;
- a reusable viewpoint.

Forcing all of these into an opinion template makes the system feel artificial and can cause the Agent to invent a claim where none is needed.

## Design Goals

- Preserve the current Capture, Theme, Library, RAG, and Review responsibilities.
- Let Garden store reusable knowledge objects, not article summaries.
- Let the Agent choose the card kind when the user does not specify one.
- Keep card types few enough that the user does not need to manage a taxonomy.
- Make card bodies human-readable in Obsidian.
- Keep every card linked back to source captures and related themes.
- Support later Canvas or visual graph views without requiring a custom frontend now.

## Non-Goals

- Do not mass-generate cards for every capture.
- Do not replace captures with cards.
- Do not turn Garden into a second Theme/MOC layer.
- Do not build a Garden frontend before Obsidian workflows prove insufficient.
- Do not create a complex maturity, confidence, or review-state taxonomy in this phase.

## Knowledge Card Kinds

Use one field, `card_kind`, with five values.

### `viewpoint`

A reusable judgment, interpretation, or stance.

Good for:

- cross-material conclusions;
- counterintuitive findings;
- user-endorsed viewpoints;
- strategic or conceptual judgments.

Not good for:

- single-article summaries;
- facts with no interpretation;
- direct excerpt collections.

### `fact`

A fact, event, case, benchmark, data point, or evidence object that is worth remembering.

Good for:

- benchmark results;
- policy changes;
- product releases;
- case examples;
- statistics and named facts.

The card should explain why the fact matters, but it does not need to become an opinion.

### `method`

A reusable way to do something.

Good for:

- workflows;
- design patterns;
- operating procedures;
- troubleshooting methods;
- evaluation or testing recipes.

Method cards should help the user apply the material later.

### `caution`

A risk, trap, boundary, anti-pattern, or thing to avoid.

Good for:

- "do not do this" lessons;
- failure modes;
- misleading assumptions;
- cases where a popular idea has limits.

Caution cards are useful because they prevent future mistakes.

### `question`

A long-lived question worth tracking.

Good for:

- open research questions;
- personal thinking prompts;
- unresolved contradictions between materials;
- topics that need future evidence.

Question cards should collect current clues without pretending the question is settled.

## Standard Properties

Garden cards should have lean but useful properties.

```yaml
id: garden-xxxx
type: garden_card
card_kind: viewpoint | fact | method | caution | question
status: seed | accepted | archived
title: 标题
source_captures:
  - capture_id
source_themes:
  - theme_id
related_cards:
  - card_id
created: 2026-05-31T00:00:00
updated: 2026-05-31T00:00:00
```

Status remains intentionally simple:

- `seed`: the card is useful enough to keep, but still open to revision.
- `accepted`: the user explicitly accepts it as a stable reusable object.
- `archived`: the card is no longer active or has been superseded.

The card's nuance should live in the body, not in more status fields.

## Body Templates

Templates should be type-aware. The Agent should not force all cards into "my judgment / why it matters".

### Viewpoint Card

```markdown
# 观点标题

## 核心观点

## 为什么这样看

## 依据

## 反例或边界

## 关联
```

### Fact Card

```markdown
# 事实标题

## 事实

## 为什么重要

## 来源依据

## 可用场景

## 关联
```

### Method Card

```markdown
# 方法标题

## 适用场景

## 做法

## 注意点

## 来源依据

## 关联
```

### Caution Card

```markdown
# 注意事项标题

## 风险

## 典型表现

## 怎么避免

## 来源依据

## 关联
```

### Question Card

```markdown
# 问题标题

## 问题

## 为什么值得追踪

## 已有线索

## 下一步观察

## 关联
```

These are default shapes, not rigid prompt modules. If the user's instruction clearly asks for another shape, the Agent should follow the user.

## Agent Responsibilities

### Capture Agent

Capture Agent remains responsible for extracting, cleaning, and organizing source material into reliable capture notes.

It should not create Garden cards by default. If the user explicitly says "把这个方法做成卡片" or "这个观点存到 Garden", the main Agent may route to Garden after Capture has a usable material note.

### Theme Agent

Theme Agent manages topic-level relationships.

It can suggest card opportunities when it sees recurring material:

- "这里可能值得提炼一张方法卡";
- "这里有一个风险提醒可以沉淀";
- "这里形成了一个观点候选";
- "这里有一个长期问题值得追踪".

Theme Agent should not directly commit final cards.

### Garden Agent

Garden Agent becomes the Knowledge Card Agent.

Responsibilities:

- Create reviewable card drafts from selected captures, themes, or user instructions.
- Decide `card_kind` when the user does not specify one.
- Generate type-aware card bodies.
- Support multi-turn revision of the same draft.
- Commit cards only after direct user confirmation.
- Keep source links and related-card links explicit.

Non-responsibilities:

- Do not summarize every capture.
- Do not mass-generate cards on a schedule.
- Do not mark a card as accepted without user confirmation.

### Library Agent

Library Agent maintains the card system after cards exist.

Responsibilities:

- Verify card frontmatter and source links.
- Ensure `source_captures` point to existing captures.
- Ensure related card links resolve.
- Maintain embeddings or section indexes for cards if needed.
- Optionally create or update Obsidian Canvas files after enough cards exist.

### RAG Agent

RAG should treat cards as high-level reusable knowledge, not as sufficient evidence by themselves.

When answering:

- If a relevant card exists, use it as an entry point.
- Expand from the card to source captures and themes for details.
- Cite the actual source material used in the answer.
- If no card exists, search themes and captures normally.

### Review Agent

Review Agent can use cards as ideal review objects because they are already small and reusable.

It should be able to pick:

- one card to explain deeply;
- one method card to walk through;
- one caution card to remind the user of a trap;
- one question card to revisit with new evidence.

Review should track exposure counts at the card level, and for long captures at the section level.

## Trigger Rules

Garden/Card creation should happen in three cases.

### Direct User Request

Examples:

- "把这个做成方法卡"
- "这个观点存到 Garden"
- "把这些文章里的注意事项提炼出来"
- "从这个主题里提炼几张知识卡"

This should create draft cards first, then wait for confirmation.

### Theme Suggestion

When Theme Agent scans related captures, it may suggest card drafts, but should not auto-commit them.

Example reply:

> 我发现这个主题下可能有 2 个值得沉淀的对象：一张方法卡和一张注意卡。要不要我先生成草稿？

### RAG/Review Discovery

If the user repeatedly asks about the same pattern and no card exists, RAG or Review may suggest creating a card.

Example:

> 这个问题已经在多篇笔记中反复出现，是否要把它整理成一张方法卡，方便以后调用？

## Obsidian and Visualization

Do not build a custom Garden frontend yet.

Use Obsidian first:

- Garden cards are normal Markdown files in `garden/`.
- Theme/MOC pages link to relevant cards.
- Cards link back to captures and related cards.
- Obsidian backlinks and graph view should reveal the basic network.

Canvas becomes useful after a topic has enough material:

- center node: Theme/MOC;
- left nodes: source captures;
- right nodes: Garden cards;
- edge labels: supports, example, risk, method, question, counterexample.

Canvas should be optional and probably user-triggered at first:

- "给这个主题生成一个 Canvas"
- "把这个 MOC 做成关系图"

This avoids producing visual clutter before the card network is mature.

## Data Model Changes

The existing `garden_cards` and `garden_drafts` tables should be extended instead of replaced.

Add:

- `card_kind TEXT NOT NULL DEFAULT 'viewpoint'`

For backward compatibility:

- Existing rows without `card_kind` become `viewpoint`.
- Existing Garden output still works.
- Future prompts and templates use `card_kind`.

The Markdown file should also contain `card_kind` in frontmatter.

## User Experience

When the user asks for cards, the reply should be short and reviewable in WeChat:

```text
我提炼出 2 张候选知识卡：

1. 方法卡：《长文整理先规划再生成》
   核心：长材料应先判断信息结构，再决定压缩粒度。

2. 注意卡：《不要把 Garden 当摘要库》
   核心：Garden 存可复用知识对象，不存文章摘要。

回复“保存第1张 / 保存全部 / 修改第2张...”即可。
```

The full card draft is stored in DB and can be expanded if needed.

## Migration Strategy

Phase 1:

- Add `card_kind` to Garden drafts/cards.
- Default existing cards to `viewpoint`.
- Update Garden prompt and markdown builder to generate type-aware cards.

Phase 2:

- Update tool descriptions so Hermes can route "方法卡 / 注意卡 / 事实卡 / 问题卡".
- Add tests for each card kind.
- Keep draft-first confirmation.

Phase 3:

- Let Theme Agent suggest card opportunities by kind.
- Let RAG use card kinds when choosing answer strategy.
- Let Review pick cards by kind and exposure count.

Phase 4:

- Add optional Obsidian Canvas generation for mature themes.

## Success Criteria

- Garden can create at least one `viewpoint`, `fact`, `method`, `caution`, and `question` draft.
- Card drafts are not committed without user confirmation.
- Existing Garden cards continue to work as viewpoint cards.
- RAG can follow a card back to source captures.
- Review can explain a card without needing to quiz the user.
- Theme/MOC pages can reference cards without becoming cluttered.
- No custom Garden frontend is required for the first version.

## Open Decision

The main naming question remains:

- Keep the public name `Garden Agent`, with `Knowledge Card` as the internal object; or
- Rename user-facing tools to `Card Agent`.

Recommendation: keep `Garden Agent` for continuity, but describe its product as Knowledge Cards.

## Self-Review

- Placeholder scan: no unfinished placeholder markers remain.
- Consistency check: Capture, Theme, Garden, Library, RAG, and Review responsibilities are separated.
- Scope check: this is a design spec only; implementation phases are listed but not expanded into tasks.
- Ambiguity check: Garden is explicitly broadened from viewpoint cards to five knowledge card kinds, while still avoiding mass generation.
