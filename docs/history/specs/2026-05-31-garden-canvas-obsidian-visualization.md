---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Garden Canvas Obsidian Visualization Design

## Summary

Garden Card visualization should use Obsidian as the main surface. Hermes should
generate optional Obsidian Canvas files that show the relationship between a
Theme/MOC, source captures, and Garden cards.

The first version should not build a custom Dashboard board. Dashboard can stay
as an auxiliary inspection surface later, but the daily knowledge experience
should remain in Obsidian.

## Problem

Garden cards are meant to make reusable knowledge objects visible: viewpoints,
facts, methods, cautions, and questions. Markdown files are readable, but the
relationship among cards, source captures, and themes can still be hard to see.

A custom Dashboard graph would duplicate Obsidian's existing workspace and add
another place for the user to inspect knowledge. Obsidian already provides
Markdown, backlinks, graph view, and Canvas. Hermes should use these features
instead of rebuilding them.

## Goals

- Make Garden relationships visually inspectable inside Obsidian.
- Keep Garden cards, captures, and themes as ordinary Markdown files.
- Generate Canvas files only when useful, not for every note by default.
- Show readable titles and wiki links, not internal IDs.
- Preserve source traceability from Garden cards back to captures.
- Let the user manually adjust the generated Canvas in Obsidian.

## Non-Goals

- Do not build a custom Garden Dashboard board in this phase.
- Do not auto-generate Canvas files for every Theme/MOC.
- Do not use Canvas as the source of truth.
- Do not edit the content of captures or Garden cards while generating Canvas.
- Do not introduce a complex visual editor inside Hermes.

## Product Positioning

Obsidian is the knowledge workspace.

- `captures/` stores processed material notes.
- `themes/` stores MOC and topic pages.
- `garden/` stores reusable knowledge cards.
- `canvas/` stores generated relationship maps that Obsidian can open.

Hermes is responsible for generating a useful first draft of the Canvas. The
user can then move nodes, delete noise, add manual annotations, or keep the
Canvas as-is.

## Triggering

Canvas generation should be manual at first.

Supported user intents:

- "给这个主题生成一个 Canvas"
- "把这个 MOC 做成关系图"
- "把 AI Agent 相关材料做成画板"
- "给这张 Garden 卡片画一下来源和关联"

The main Agent should not create Canvas files silently after every Capture,
Theme scan, or Garden card commit.

## First Version Scope

The first implementation should support generation from a Theme/MOC or a theme
candidate group.

Input:

- a `theme` identifier, MOC path, or natural-language topic;
- optionally a list of capture IDs or Garden card IDs if the previous tool
  result already provides them.

Output:

- one `.canvas` file under the Obsidian vault `canvas/` directory;
- a chat preview that gives the file path and a short explanation of what was
  included;
- a durable artifact reference for run ledger and future debugging.

## Canvas Layout

The default Canvas should use a simple three-zone layout.

Left:

- source captures;
- each node links to the capture Markdown file;
- long titles are shortened visually but keep full readable titles in metadata.

Center:

- Theme/MOC node;
- this is the visual anchor of the Canvas.

Right:

- Garden cards related to the theme or source captures;
- each node links to the Garden Markdown file;
- node color or group can reflect `card_kind`.

Optional bottom area:

- open questions;
- warnings about weak or missing links;
- notes that need user judgment.

## Node Types

Use Obsidian Canvas file nodes when a Markdown file exists.

Capture node:

- type: file
- file: `captures/<title>.md`
- label: readable capture title

Theme node:

- type: file
- file: `themes/<moc>.md`
- label: readable theme title

Garden node:

- type: file
- file: `garden/<title>.md`
- label: readable Garden card title

Text node:

- use only for section labels or warnings;
- do not use text nodes to duplicate full note content.

## Edge Semantics

Obsidian Canvas edges are visual aids, not database truth. Hermes should infer
lightweight relationship labels when possible.

Recommended labels:

- `evidence`: capture supports the Theme/MOC.
- `distills`: Theme/MOC or captures are distilled into a Garden card.
- `method`: Garden card captures a method.
- `risk`: Garden card captures a caution or boundary.
- `example`: capture provides an example.
- `question`: card or capture raises an unresolved question.
- `related`: fallback when the exact relation is unclear.

If the system is uncertain, prefer `related` over inventing a precise relation.

## Data Sources

Use existing Hermes data first.

Primary data:

- `garden_cards.capture_ids`
- `garden_cards.knowledge_links`
- `garden_cards.card_kind`
- `captures.file_path`
- Theme/MOC files in `themes/`
- pending Theme candidates and their `related_capture_ids`

Secondary data:

- `capture_embeddings` identity and section rows;
- `theme_scanned_at`;
- `agent_runs` evidence for recent Theme/Garden operations.

The Canvas generator should not require re-reading full raw source text.

## File Naming

Canvas file names should be readable.

Format:

`<主题标题>-关系图.canvas`

Examples:

- `AI Agent工程化-关系图.canvas`
- `知识管理的本质-关系图.canvas`

If a file already exists, the tool can update it when the user asks to refresh
the same topic. It should not create many timestamped duplicates by default.

## Updating Existing Canvas Files

First version can use replace-in-place.

Rules:

- if the Canvas was generated by Hermes and has matching metadata, update it;
- if the Canvas appears manually created or lacks Hermes metadata, create a new
  file with a short suffix such as `-Hermes`;
- do not overwrite a user-edited Canvas unless it is clearly a Hermes-managed
  file.

Hermes metadata can live in the Canvas JSON as a small text node or custom
fields attached to nodes where Obsidian tolerates them. The implementation must
verify the actual Canvas format before relying on custom fields.

## User-Facing Reply

After generation, the WeChat reply should be short.

Example:

```text
已生成 Obsidian Canvas：《AI Agent工程化-关系图》

里面包含：
- 1 个主题页
- 5 篇来源笔记
- 3 张 Garden 卡

你可以在 Obsidian 的 canvas/ 文件夹里打开它继续调整。
```

Do not paste the whole Canvas JSON into chat.

## Error Handling

No matching theme:

- say that no matching Theme/MOC was found;
- suggest scanning Theme first or naming specific notes.

No Garden cards:

- still generate a Capture + Theme Canvas if there are enough source captures;
- tell the user that no Garden cards were linked yet.

Too many nodes:

- cap the first version at a readable size, for example one Theme, up to 10
  captures, and up to 8 Garden cards;
- tell the user the Canvas was trimmed and can be regenerated with a narrower
  topic.

Missing files:

- skip missing Markdown file nodes;
- report skipped items in the chat preview and run metadata.

## Architecture

Add a small Canvas generation layer rather than a large new Agent.

Suggested boundary:

- `agents/canvas_agent.py`
  - builds a Canvas graph model;
  - chooses layout positions;
  - serializes to Obsidian Canvas JSON.

- `bridge/canvas_bridge.py`
  - resolves user topic to Theme/MOC, captures, and Garden cards;
  - writes the `.canvas` file to the vault;
  - returns a result envelope.

- runtime tool in `hermes-agent-main`
  - `generate_canvas`
  - routes user requests such as "生成画板" or "做成关系图".

This keeps Canvas generation separate from Theme and Garden logic while still
letting those agents provide source relationships.

## Testing

Unit tests:

- serializes valid Canvas JSON with file nodes and edges;
- positions source captures left, Theme/MOC center, Garden cards right;
- uses readable labels and file paths, not raw IDs;
- caps large graphs and reports trimmed nodes;
- handles missing files without crashing.

Bridge tests:

- generates a Canvas from a Theme/MOC and linked Garden cards;
- generates a Canvas from a pending Theme candidate group;
- avoids overwriting a non-Hermes Canvas;
- returns a concise chat preview with artifact path.

Runtime tests:

- tool schema routes "生成 Canvas / 关系图 / 画板" to `generate_canvas`;
- tool output is passed through as a user-visible reply;
- no Canvas JSON leaks into WeChat.

Manual smoke test:

1. Ask Hermes: "把 AI Agent 这个主题做成关系图。"
2. Confirm a `.canvas` file appears in the Obsidian vault.
3. Open it in Obsidian.
4. Confirm nodes use readable titles.
5. Confirm file nodes open the correct Capture, Theme, and Garden Markdown
   files.

## First-Version Defaults

Default node limits are conservative in the first version: one Theme/MOC, up to
10 captures, and up to 8 Garden cards. A visually useful Canvas is better than
a complete but unreadable one.

The first implementation should start with Theme/MOC centered Canvas generation.
Garden-card-centered expansion can come next.

Dashboard visualization should stay out of this phase. If later needed, it can
be a management/debugging surface rather than the primary knowledge workspace.
