# DEPRECATED

This custom Next.js dashboard has been **deprecated** as of 2026-06-16.

## Why

The native Hermes Dashboard (port 9119) already provides equivalent or superior
functionality for all pages here:

| Custom page | Native equivalent |
|---|---|
| `/` (Capture Timeline) | *No equivalent yet — candidate for dashboard plugin* |
| `/knowledge-chat` | `/knowledge-chat` (JSON-RPC WS, tool traces, source panel) |
| `/drafts` | Built into `/knowledge-chat` + `/capture-review` |
| `/garden` | `/cards-board` (richer UI) |

The design doc (`docs/superpowers/specs/2026-06-14-hermes-web-full-chat-design.md`)
mandates: **"方案 A：复用 Hermes Web Chat，并增强知识库侧栏"**

## How to use the native dashboard

```bash
cd ~/Downloads/hermes-agent-main
.venv/bin/python hermes dashboard
# → http://127.0.0.1:9119
```

## What's left

The only unique feature — Capture Timeline with tag/folder/search browsing —
could be ported as a native dashboard plugin if needed.
