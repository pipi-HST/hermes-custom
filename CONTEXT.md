---
status: current
owner: knowledge-service
last_verified: 2026-11-18
verified_against: "3e6b974 (hermes-custom) + 2026-11-18 并行修复分支"
---

# Hermes Knowledge Context

> **当前术语表：** [GLOSSARY.md](GLOSSARY.md)。
> **当前架构：** [ARCHITECTURE.md](ARCHITECTURE.md)。
> `Knowledge Map / Knowledge Area / Map Dimension / Map Entry / Map Proposal / Knowledge Health Check / Health Report` 都是 **deferred 设计稿词汇**，没有代码、表或接口；不要用它们描述已实现的 `Topic` 或 `Theme View`。

Hermes is a personal knowledge-management system where WeChat/Hermes inputs become a reviewable Knowledge Workflow: confirmed material may become Knowledge Files, while reusable views, cards, graph relationships, and review state live as structured Web/DB objects.

## Product Workflow

Hermes Knowledge should feel like one workflow to the user:

```text
material or question
  -> AI reads or discusses with sources
  -> a draft is produced
  -> the user reviews and confirms
  -> durable knowledge is written
  -> the knowledge can be cited and reused later
```

User-facing writes should follow this rule: AI may prepare a draft without confirmation, but AI must not create, overwrite, repair, or delete durable knowledge without a confirmable action.

Long answers, extracted material, and generated reports should be available as durable artifacts or full views. A chat reply is only a preview, not the source of truth.

## Product Vocabulary

Implemented user-facing terms:

| Term | User meaning | Internal implementation may include |
| --- | --- | --- |
| Capture | Saved source material and its processed note | Capture Agent, extractors, raw archive, capture draft |
| Reading Session | A temporary discussion around one or more sources | reading sessions, turns, evidence, reading drafts |
| Draft | Reviewable AI output before durable write | capture drafts, reading drafts, garden drafts |
| Knowledge File | A persistent Markdown knowledge file | Capture Note, Raw Evidence |
| Topic | The folder-based subject a Capture or Knowledge Card belongs to | `folder_path`, `capture_metadata.folder_path`, `garden_cards.source_theme`, `knowledge_folders.json` |
| Theme View | An advanced view that groups material around a theme | theme scan, `pending_questions`, `knowledge_edges`, optional Markdown export |
| Knowledge Card | A structured reusable knowledge object | Garden Card, `garden_cards`, card payload JSON |
| Library | Browse, filter and edit committed material and topics | library views, `capture_metadata` |
| Review | Scheduled recall of knowledge points | `review_items`, daily review |
| Research | Answer questions from local knowledge plus public web sources | research bridge, `agents/research`, run ledger |
| Source | Citable evidence used by AI output | Capture, Capture Section, Theme View, Knowledge Card, Source Map |

Avoid making normal users think in these implementation terms by default: Agent names, Theme scan, MOC, Canvas, Run Ledger, embedding index, deferred map vocabulary, or raw database table names.

## Deferred Design Vocabulary

These are design-draft terms for a possible future navigation model. **None of them is implemented**: there is no table, API, or user surface for them in the current service. ADR 0005 remains deferred. Do not use them as default user words, and do not confuse them with the implemented **Topic** or **Theme View**.

| Term | Design intent (not current behavior) |
| --- | --- |
| Knowledge Map | A confirmed navigation structure for Hermes Knowledge, divided into Knowledge Areas, dimensions, and entries |
| Knowledge Area | A top-level library-like area inside the Knowledge Map |
| Map Dimension | A confirmed index axis inside one Knowledge Area |
| Map Entry | A named entry under one Map Dimension |
| Map Proposal | A reviewable AI suggestion for changing the Knowledge Map |
| Knowledge Health Check | A user-invoked assessment of map and material structure |
| Health Report | The readable output of a Knowledge Health Check |

## Language

**Capture**:
A single intake action that starts from user-provided material and may produce a **Capture Draft** before anything is committed to Knowledge storage.
_Avoid_: using "capture" to mean the agent, the database table, or the final Markdown note.

**Capture Draft**:
An uncommitted working version of captured material that can be revised and confirmed before Knowledge File write.
_Avoid_: note, capture note, raw.

**Knowledge File**:
A persistent Markdown knowledge file under Hermes Knowledge storage. In the primary model, only **Capture Note** and **Raw Evidence** are standing Knowledge Files.
_Avoid_: treating every knowledge object as a Markdown file, or using Obsidian vault as the storage truth.

**Capture Note**:
The committed, processed material note stored in `captures/`; it is readable evidence or material, not the user's accepted viewpoint.
_Avoid_: capture, garden card, raw note.

**Capture Section**:
A named, addressable section inside a long **Capture Note**, usually reflected in the note outline and used by RAG or Review to avoid reading the whole note.
_Avoid_: arbitrary paragraph, chunk, full capture note.

**Raw Evidence**:
The committed source-preservation note stored in `raw/` or an archive raw area; it exists for traceability and is not the daily reading surface.
_Avoid_: capture note, cache, daily note.

**Theme** (legacy alias; the current user word is 专题 — see GLOSSARY):
A topic concept that groups related **Capture Notes** around a recurring subject, question, or mechanism.
_Avoid_: MOC, tag, garden card.

**Theme View**:
An advanced view that groups related material, open questions, and graph relationships around a **Theme** without requiring a standing Markdown page. Its real storage is `pending_questions`, `knowledge_edges`, and `captures.theme_scanned_at`; Markdown under `themes/` is only an optional export. It is not the default word for **Topic**.
_Avoid_: using Theme View as the default word for 专题, Theme Page as the primary business term, MOC, garden card.

**Theme Page**:
The legacy name for a Markdown export or older Obsidian-backed representation of a **Theme View**.
_Avoid_: using Theme Page for the current product model unless explicitly discussing legacy files or export.

**MOC**:
A legacy page structure that links related notes and questions; it is a writing/export pattern, not the main domain term.
_Avoid_: using MOC when the domain meaning is Theme or Theme View.

### Deferred design vocabulary (not implemented)

The following definitions are design notes only. They are not current product behavior, not default user vocabulary, and have no table or API.

**Knowledge Map**:
The proposed navigation structure for the Hermes Knowledge context. In the design draft, Hermes Knowledge has one Knowledge Map, divided into **Knowledge Areas** before area-specific dimensions and entries.
_Avoid_: describing current behavior; graph, MOC, folder tree, multiple competing maps, separate knowledge base, deleting underlying material when deleting map structure.

**Knowledge Area**:
A top-level library-like area inside the **Knowledge Map**, such as high-quality human interviews, animal encyclopedia, or AI product materials. It may be created manually before dimensions exist, may later receive AI-proposed dimensions, and the same material may belong to multiple Knowledge Areas.
_Avoid_: separate Knowledge Base, folder, Theme View, global map dimension.

**Map Dimension**:
A confirmed index axis inside one **Knowledge Area**, such as people, themes, viewpoint conflicts, animal taxonomy, or habitats. Dimensions are proposed by AI but become stable user-confirmed language after acceptance.
_Avoid_: tag, folder, mutually exclusive category tree, temporary weekly activity, global dimension shared by every area.

**Map Entry**:
A named entry under one **Map Dimension**, such as a person, topic, conflict, species, or habitat. Confirmed material assignments attach to Map Entries, and a Map Entry may link to a **Theme View** when it needs a deeper topic workspace.
_Avoid_: file, folder, graph node, direct material assignment to a Knowledge Area, or treating every Map Entry as a Theme View.

**Map Proposal**:
A reviewable state object containing AI suggestions to create or change **Knowledge Areas**, dimensions, entries, or material assignments based only on confirmed material. It is not a Durable Artifact and is not durable map structure until the user accepts it.
_Avoid_: automatic map update, final map, report artifact.

**Knowledge Health Check**:
A user-invoked Theme Agent assessment of confirmed Hermes Knowledge structure. It may produce a **Health Report** and **Map Proposal**, but it does not automatically change the **Knowledge Map**.
_Avoid_: scheduled automatic map update, Knowledge Map, graph report.

**Health Report**:
The durable readable output of a **Knowledge Health Check**, summarizing coverage, unassigned material, and suggested follow-up. It is not itself part of the long-term **Knowledge Map**.
_Avoid_: Knowledge Map, permanent dimension, Map Entry.

### Implemented domain terms (continued)

**Open Question**:
An unresolved question kept in a **Theme View** when there is not yet a current viewpoint or hypothesis.
_Avoid_: garden card, claim.

**Knowledge Card**:
A reusable structured knowledge object. It can contain a viewpoint, fact, method, risk, framework, or judgment the user is shaping or has accepted.
_Avoid_: summary, theme page, pure question, capture note.

**Garden Card**:
The legacy/internal name for a **Knowledge Card**, kept because some code and database tables still use it.
_Avoid_: using Garden Card as the default user-facing product term, or implying Knowledge Cards are standing `garden/` Markdown files.

**Seed Garden Card**:
A **Knowledge Card** the user allowed into the garden for refinement, but which does not yet represent the user's accepted view.
_Avoid_: accepted view, final opinion.

**Accepted Garden Card**:
A **Knowledge Card** the user has explicitly accepted as their current view or reusable framework.
_Avoid_: seed, candidate, archived view.

**Archived Garden Card**:
A **Knowledge Card** kept for history but no longer used as the user's current view.
_Avoid_: accepted view, active viewpoint.

**Review Item**:
The smallest useful unit for later recall. For a short **Capture Note**, one Review Item may cover the whole note; for a long Capture Note, one Review Item should usually point to a specific section.
_Avoid_: treating every full article as one fixed review unit.

**Source Map**:
The compact source list attached to a RAG answer, showing where the answer's viewpoint, structure, and evidence came from.
_Avoid_: untyped source list, vague citation list.

**Source Reference**:
A structured entry inside a **Source Map** that records the source role, object type, id, title, path, optional section, and relevant status.
_Avoid_: plain filename string, untyped citation.

**Viewpoint Source**:
The **Knowledge Card** source for a claim about the user's view. Only an **Accepted Garden Card** can be used as the user's accepted viewpoint source.
_Avoid_: using seed cards or capture notes as accepted viewpoint sources.

**Structure Source**:
The **Theme View** source that organizes the answer's topic boundaries or conceptual structure.
_Avoid_: evidence source.

**Evidence Source**:
The **Capture Note** or **Capture Section** source that supports answer details, examples, facts, or cases.
_Avoid_: viewpoint source, structure source.

**Verification Source**:
The **Raw Evidence** source used to check original wording or trace a claim back to source material. It is not part of normal RAG evidence unless verification is needed.
_Avoid_: default evidence source, daily reading source.

**Durable Artifact**:
A long-lived result created by an agent, such as a Capture Draft, Capture Note, Raw Evidence, Theme View, Knowledge Card, Librarian Report, Review Trace, or another structured object.
_Avoid_: chat message, preview, temporary reply.

**Durable Artifact Reference**:
The stable reference to a **Durable Artifact** inside an **Agent Result Envelope**, with consistent fields for type, id, title, path, and status.
_Avoid_: agent-specific id fields such as draft_id, card_id, or report_path at the top level.

**Chat Preview**:
The short channel-friendly message returned to WeChat or another chat surface to summarize a **Durable Artifact** and offer next actions.
_Avoid_: full artifact, final storage, source of truth.

**Agent Result Envelope**:
The structured JSON object an agent or bridge returns to the Hermes gateway/main agent, carrying a Chat Preview, Durable Artifact references, Source Map, next actions, warnings, and errors.
_Avoid_: plain string result, unstructured tool response.

An Agent Result Envelope always has stable top-level fields: `status`, `agent`, `intent`, `chat_preview`, `durable_artifacts`, `source_map`, `next_actions`, `warnings`, `errors`, and `meta`. Fields may be empty, but agents should not invent incompatible response shapes.

**Next Action**:
A structured follow-up option inside an **Agent Result Envelope** that includes a user-facing label, a machine-readable intent, optional payload, and whether explicit user confirmation is required.
_Avoid_: free-text suggestion, hidden tool call.

Next Actions that write durable artifacts, delete or repair data, create knowledge objects, or change knowledge state must require explicit confirmation. Read-only expansion actions may skip confirmation.

## Flagged Ambiguities

- **Knowledge Map**, **Knowledge Area**, **Map Dimension**, **Map Entry**, **Map Proposal**, **Knowledge Health Check**, and **Health Report** are deferred design-draft terms. No current table, API, or user surface implements them. Do not use them as default words for **Topic** or **Theme View**.
- "Capture" was previously used for an agent, an action, a directory, a SQLite table, and a Markdown note. Resolved: use **Capture** for the intake action, **Capture Draft** for pre-commit working material, and **Capture Note** for the committed processed Markdown note.
- "Theme" and "MOC" were previously mixed. Resolved: **Topic** is the default folder-based user word; **Theme View** is the implemented advanced view; **Theme Page** is legacy/export language; **MOC** is a legacy page structure.
- "Garden Card" is now treated as an implementation/legacy name for **Knowledge Card**. Use **Knowledge Card** in normal user-facing copy unless referring to code or database tables.
- "Knowledge Graph" and **Knowledge Map** must not be mixed. A Knowledge Graph is the implemented relationship browser over `knowledge_edges`; a Knowledge Map is only a deferred design term.
- **Knowledge Health Check** and **Knowledge Map** are both deferred; if that model is ever built, a Health Check may produce a report and proposal but must not automatically rewrite confirmed structure.
- A **Health Report** is a deferred design artifact, not a current Knowledge Map object and not default RAG evidence.
- A **Map Proposal** is a deferred reviewable state object, not a **Durable Artifact**.
- **Map Entry** and **Theme View** are both design-adjacent terms, but only **Theme View** is implemented. Do not describe a current Theme View as a Map Entry.
- "Knowledge Base" is not a first-version Knowledge domain boundary. In the current product, use **Library** for browsing and **Topic** for folder-based grouping; **Knowledge Area** belongs to the deferred map design only.
- **Knowledge Area** is a deferred design term, not a current folder or storage boundary.
- **Map Dimension** is a deferred design term, not a current category tree.
- A **Knowledge Card** can look like a long-running question, but a pure unresolved question belongs as an **Open Question** in a **Theme View**. A Knowledge Card must contain reusable knowledge, not only a question.
- **Obsidian vault** is legacy storage language. It may be used as an optional external Markdown editor, but it is no longer the primary product carrier or storage truth.
- A **Seed Garden Card** is not the user's accepted view. Only an **Accepted Garden Card** may be described by RAG as "the user's view."
- A **Review Item** is sized by usefulness for recall. It may be a whole short Capture Note, but long Capture Notes should produce section-level Review Items.
- A **Capture Section** is not an arbitrary chunk. It is a named section from the Capture Note outline that agents can cite, retrieve, and review.
- A **Source Map** should distinguish **Viewpoint Source**, **Structure Source**, and **Evidence Source** instead of listing undifferentiated files.
- A **Source Map** is made of structured **Source References**, not free-form strings.
- **Raw Evidence** is a **Verification Source**, not the default **Evidence Source** for RAG answers.
- A **Durable Artifact** is not the same as a **Chat Preview**. The artifact is the long-lived result; the preview is only the short channel response.
- A **Durable Artifact Reference** should use the same structure across agents so the gateway does not need agent-specific parsing.
- An **Agent Result Envelope** is the handoff contract between sub-agents and the Hermes gateway/main agent. The main agent should route and display it, not reinterpret the knowledge inside it.
- Agent Result Envelope fields are stable even when empty, so the Hermes gateway/main agent does not have to guess each agent's response shape.
- A **Next Action** must be both readable by the user and routable by the Hermes gateway/main agent.
- A write or state-changing **Next Action** must require explicit user confirmation before execution.

## Example Dialogue

Dev: "When the user sends an article, do we create a Capture Note immediately?"

Domain expert: "No. The Capture creates a Capture Draft first. After the user confirms, Hermes writes Raw Evidence and a Capture Note."

Dev: "Can a Capture Note contain my final opinion?"

Domain expert: "No. A Capture Note is processed material. A Knowledge Card is where accepted or evolving reusable knowledge lives."

Dev: "Should the agent create a MOC for this?"

Domain expert: "Say Topic for the folder-based subject. If you need the advanced aggregation, say Theme View. Markdown export may use a MOC-like structure, but MOC is not the domain word."

Dev: "Can we make a Knowledge Card called 'Why does AI engineering get stuck in organizations?'"

Domain expert: "Only if it has a current hypothesis. If it is just a question, keep it as an Open Question in the Theme View."

Dev: "Can RAG say this seed card is the user's view?"

Domain expert: "No. It can say there is a Seed Garden Card being refined. Only an Accepted Garden Card is the user's view."

Dev: "Is the whole article always one Review Item?"

Domain expert: "No. A short Capture Note can be one Review Item. A long Capture Note should usually create Review Items for specific sections."

Dev: "When RAG needs part of a long Capture Note, what should it cite?"

Domain expert: "It should cite the Capture Note and the relevant Capture Section, not an invisible chunk."

Dev: "Can the RAG answer just list three files at the end?"

Domain expert: "No. It should provide a Source Map: which source gave the viewpoint, which gave the structure, and which gave the evidence."

Dev: "Can a source just be a path string?"

Domain expert: "No. It should be a Source Reference with role, type, id, title, path, section when relevant, and status when relevant."

Dev: "Should RAG use Raw Evidence as its normal evidence?"

Domain expert: "No. RAG should normally use Capture Notes or Capture Sections as Evidence Sources. Raw Evidence is a Verification Source when original wording or traceability matters."

Dev: "Is the WeChat reply the artifact?"

Domain expert: "No. The artifact is stored durably. The WeChat reply is only a Chat Preview."

Dev: "Can Capture return draft_id while Garden returns card_id?"

Domain expert: "They can keep those in metadata if needed, but the envelope should expose Durable Artifact References with consistent fields."

Dev: "Can a bridge just return a string?"

Domain expert: "No. It should return an Agent Result Envelope so the gateway knows the preview, artifacts, source map, next actions, warnings, and errors separately."

Dev: "Can next actions just say 'you can save this'?"

Domain expert: "No. A Next Action needs a label for the user and an intent plus payload for the gateway."
