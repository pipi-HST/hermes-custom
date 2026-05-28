# Hermes Knowledge Context

Hermes is a personal knowledge-management system where WeChat/Hermes inputs become Obsidian notes, and agents maintain distinct knowledge objects around those notes.

## Language

**Capture**:
A single intake action that starts from user-provided material and may produce a **Capture Draft** before anything is committed to the vault.
_Avoid_: using "capture" to mean the agent, the database table, or the final Markdown note.

**Capture Draft**:
An uncommitted working version of captured material that can be revised and confirmed before vault write.
_Avoid_: note, capture note, raw.

**Capture Note**:
The committed, processed material note stored in `captures/`; it is readable evidence or material, not the user's accepted viewpoint.
_Avoid_: capture, garden card, raw note.

**Capture Section**:
A named, addressable section inside a long **Capture Note**, usually reflected in the note outline and used by RAG or Review to avoid reading the whole note.
_Avoid_: arbitrary paragraph, chunk, full capture note.

**Raw Evidence**:
The committed source-preservation note stored in `raw/` or an archive raw area; it exists for traceability and is not the daily reading surface.
_Avoid_: capture note, cache, daily note.

**Theme**:
A topic concept that groups related **Capture Notes** around a recurring subject, question, or mechanism.
_Avoid_: MOC, tag, garden card.

**Theme Page**:
The Obsidian Markdown page for a **Theme**, stored under `themes/` and usually written as a map of content.
_Avoid_: MOC as the primary business term, garden card.

**MOC**:
A page structure for a **Theme Page** that links related notes and questions; it is a writing pattern, not the main domain term.
_Avoid_: using MOC when the domain meaning is Theme or Theme Page.

**Open Question**:
An unresolved question kept on a **Theme Page** when there is not yet a current viewpoint or hypothesis.
_Avoid_: garden card, claim.

**Garden Card**:
A viewpoint object stored under `garden/` that contains a current claim, hypothesis, framework, or judgment the user is shaping or has accepted.
_Avoid_: summary, theme page, pure question, capture note.

**Seed Garden Card**:
A **Garden Card** the user allowed into the garden for refinement, but which does not yet represent the user's accepted view.
_Avoid_: accepted view, final opinion.

**Accepted Garden Card**:
A **Garden Card** the user has explicitly accepted as their current view or reusable framework.
_Avoid_: seed, candidate, archived view.

**Archived Garden Card**:
A **Garden Card** kept for history but no longer used as the user's current view.
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
The **Garden Card** source for a claim about the user's view. Only an **Accepted Garden Card** can be used as the user's accepted viewpoint source.
_Avoid_: using seed cards or capture notes as accepted viewpoint sources.

**Structure Source**:
The **Theme Page** source that organizes the answer's topic boundaries or conceptual structure.
_Avoid_: evidence source.

**Evidence Source**:
The **Capture Note** or **Capture Section** source that supports answer details, examples, facts, or cases.
_Avoid_: viewpoint source, structure source.

**Verification Source**:
The **Raw Evidence** source used to check original wording or trace a claim back to source material. It is not part of normal RAG evidence unless verification is needed.
_Avoid_: default evidence source, daily reading source.

**Durable Artifact**:
A long-lived result created by an agent, such as a Capture Draft, Capture Note, Raw Evidence, Theme Page, Garden Card, Librarian Report, or Review Trace.
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

- "Capture" was previously used for an agent, an action, a directory, a SQLite table, and a Markdown note. Resolved: use **Capture** for the intake action, **Capture Draft** for pre-commit working material, and **Capture Note** for the committed processed Markdown note.
- "Theme" and "MOC" were previously mixed. Resolved: **Theme** is the topic concept, **Theme Page** is its Obsidian page, and **MOC** is the page structure.
- "Garden Card" can look like a long-running question, but a pure unresolved question belongs as an **Open Question** on a **Theme Page**. A Garden Card must contain a current viewpoint or hypothesis.
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

Domain expert: "No. A Capture Note is processed material. A Garden Card is where accepted or evolving viewpoints live."

Dev: "Should the agent create a MOC for this?"

Domain expert: "Say Theme Page. The page may use a MOC structure, but the domain object is the Theme Page."

Dev: "Can we make a Garden Card called 'Why does AI engineering get stuck in organizations?'"

Domain expert: "Only if it has a current hypothesis. If it is just a question, keep it as an Open Question on the Theme Page."

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
