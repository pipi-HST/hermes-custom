---
status: historical
execution_status: done
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Garden Card Canvas Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Garden-card-first Obsidian Canvas view that clusters Garden cards directly instead of requiring a Theme/MOC as the center.

**Architecture:** Keep deterministic Canvas JSON building in `agents/canvas_agent.py`, and keep data resolution/tool envelope logic in `bridge/canvas_bridge.py`. LLM planning remains optional and only explains card relationships; deterministic code owns DB reads, Obsidian links, layout, source labels, and avoiding raw IDs in user-facing text.

**Tech Stack:** Python, SQLite, Obsidian Canvas JSON, pytest.

---

### Task 1: Garden-Only Canvas Entry

**Files:**
- Modify: `bridge/canvas_bridge.py`
- Test: `tests/test_canvas_bridge.py`

- [ ] **Step 1: Write failing bridge test**

Add a test that inserts Garden cards and captures, calls `generate_canvas(garden_card_ids=[...])` with no theme, and expects a Canvas file named `Garden Card 聚类-关系图.canvas`.

- [ ] **Step 2: Implement Garden-only data resolution**

When no Theme/MOC is found but `garden_card_ids` are present, resolve cards first, derive capture IDs from those cards, build a synthetic `CanvasTheme` titled `Garden Card 聚类`, and continue through the structured Canvas path.

- [ ] **Step 3: Verify focused bridge tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_canvas_bridge.py -q
```

Expected: all bridge Canvas tests pass.

### Task 2: Readable Garden Card Layout

**Files:**
- Modify: `agents/canvas_agent.py`
- Test: `tests/test_canvas_agent.py`

- [ ] **Step 1: Write/update Canvas builder tests**

Assert Garden cards are large readable nodes, source badges do not overlap card text, all source pills are shown, and no internal IDs appear in visible node text.

- [ ] **Step 2: Implement layout adjustments**

Keep Garden card nodes as the largest visual objects, place S1/S2 source badges below each card, and place all source links in a bottom evidence panel.

- [ ] **Step 3: Verify focused Canvas tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_canvas_agent.py -q
```

Expected: all Canvas builder tests pass.

### Task 3: Smoke Test Real Vault Output

**Files:**
- No new production files expected.

- [ ] **Step 1: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_canvas_agent.py tests/test_canvas_bridge.py -q
```

- [ ] **Step 2: Generate a real Garden Canvas dry smoke**

Call `generate_canvas` against the local DB/vault using existing Garden cards and inspect the generated `.canvas` JSON for readable Garden titles and Obsidian links.

- [ ] **Step 3: Restart Hermes**

After code changes, clear/backup Hermes sessions, restart the API and gateway, and verify a single gateway/API process is running.
