---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Hermes Web Full Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full-length Hermes Web chat page for knowledge workflows so long answers, draft previews, sources, and tool traces can be inspected without WeChat message limits.

**Architecture:** Add a new React page in `hermes-agent-main/web` that talks to the existing Hermes gateway through `GatewayClient`. Keep the existing terminal `/chat` route unchanged, and reuse the same Hermes main agent, toolset, bridge, and JSON-RPC event stream instead of creating a parallel knowledge backend.

**Tech Stack:** React 19, TypeScript, Vite, Tailwind 4, lucide-react, existing Hermes `tui_gateway` JSON-RPC events.

---

## Files

- Create: `/Users/pipi/Downloads/hermes-agent-main/web/src/lib/knowledgeChatState.ts`
  - Pure state reducer for GUI transcript, streaming assistant text, tool calls, and source hints.
- Create: `/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx`
  - The Web “full chat” workbench with transcript, composer, tool trace panel, and source panel.
- Modify: `/Users/pipi/Downloads/hermes-agent-main/web/src/App.tsx`
  - Add `/knowledge-chat` route and sidebar entry while preserving `/chat`.
- Verify: `/Users/pipi/Downloads/hermes-agent-main/web`
  - `npm run build`

## Assumptions

- `session.create` does not need a custom toolset parameter; it uses the runtime’s configured Hermes agent/toolset.
- `prompt.submit` returns immediately with `{status: "streaming"}` and final text arrives through `message.complete`.
- Tool events have the same payload shape already used by `ChatSidebar`: `tool.start`, `tool.progress`, `tool.complete`.
- Source extraction in v0.1 is best-effort from answer text and tool summaries. Business-grade citation logic remains in the existing RAG/Capture tools.

## Task 1: GUI Chat State Reducer

**Files:**
- Create: `/Users/pipi/Downloads/hermes-agent-main/web/src/lib/knowledgeChatState.ts`

- [x] **Step 1: Define state types**

Create `KnowledgeMessage`, `KnowledgeSourceHint`, `KnowledgeChatState`, and `KnowledgeChatAction` types. Include:
- user and assistant messages
- streaming assistant message
- connection/session/running/error fields
- tool entries compatible with existing `ToolCall`
- source hints extracted from assistant text and tool summaries

- [x] **Step 2: Implement reducer behavior**

Add actions for:
- `connection`
- `session_created`
- `submit_user`
- `message_start`
- `message_delta`
- `message_complete`
- `tool_start`
- `tool_progress`
- `tool_complete`
- `error`
- `reset_error`

Expected behavior:
- Submitting user text appends one user message, clears error, marks running.
- Streaming deltas append to a single draft assistant message.
- `message.complete` replaces the draft with the final assistant message, marks running false, and extracts source hints.
- Tool starts/progress/completes update one entry per `tool_id`.
- Errors are visible and mark running false.

- [x] **Step 3: Keep extraction deterministic and small**

Implement `extractSourceHints(text, toolSummaries)` with simple stable rules:
- capture lines under `参考来源` / `来源` headings
- capture Markdown links
- capture bracket citations like `[1] Title`
- dedupe by label + url
- cap to 12 hints

Do not add LLM calls or business rules here.

## Task 2: Knowledge Chat Page

**Files:**
- Create: `/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx`

- [x] **Step 1: Connect to Hermes gateway**

Use `GatewayClient` directly:
- create client with `useMemo`
- connect on mount
- call `session.create`
- subscribe to `message.start`, `message.delta`, `message.complete`, `tool.start`, `tool.progress`, `tool.complete`, and `error`
- close client on unmount

- [x] **Step 2: Build the transcript**

Render:
- user messages as compact right-aligned bubbles
- assistant messages as full-width Markdown blocks
- streaming assistant message with caret
- empty state with three practical prompts:
  - “基于知识库回答这个问题：”
  - “展开最近的 Capture 草稿”
  - “扫描最近保存的内容，看看有没有可整理的关联”

- [x] **Step 3: Build the composer**

Render a textarea and send button:
- Enter sends
- Shift+Enter inserts newline
- disabled while connection is not open, session is missing, or a turn is running
- submit through `prompt.submit` with `{session_id, text}`

- [ ] **Step 4: Build side panels**

Right side should show:
- Sources: extracted source hints from the current answer
- Tools: existing `ToolCall` rows for recent tool calls
- Session status: connection state, session id, and whether a turn is running

The page should be readable for long Markdown, not terminal-like.

## Task 3: Route and Navigation

**Files:**
- Modify: `/Users/pipi/Downloads/hermes-agent-main/web/src/App.tsx`

- [x] **Step 1: Import the page**

Import `KnowledgeChatPage` and a suitable lucide icon.

- [x] **Step 2: Add route**

Add `"/knowledge-chat": KnowledgeChatPage` to built-in routes.

- [x] **Step 3: Add nav item**

Add a sidebar item labelled `Knowledge Chat` near `/chat`. Preserve the existing `/chat` terminal route and its persistent host logic.

## Task 4: Verification and Runtime Smoke Test

**Files:**
- Verify only unless build exposes a defect.

- [x] **Step 1: Build the web app**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main/web
npm run build
```

Expected:
- TypeScript build succeeds.
- Vite build succeeds.

- [ ] **Step 2: Restart Hermes with clean sessions**

Use the Hermes restart hygiene flow:
- back up or clear `/Users/pipi/.hermes/sessions`
- restart the gateway/dashboard process
- verify the process and logs show it is running

- [ ] **Step 3: Browser smoke test**

Open:

```text
http://localhost:<dashboard-port>/knowledge-chat
```

Verify:
- page loads
- session id appears
- sending a short prompt streams or returns an assistant message
- tool calls appear in the side panel when tools are used
- long answer area is not constrained like WeChat

## Out of Scope

- New business APIs for Capture/RAG/Garden.
- Login, multi-user permissions, or Obsidian editing UI.
- Replacing WeChat as the default low-friction input.
- Perfect citation parsing in the Web layer.

