# Garden Card Board Canvas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn generated Obsidian Canvas files from Theme/MOC-style relationship maps into Garden Card boards where Garden cards are the main visual objects and source notes are compact evidence entries.

**Architecture:** Keep the existing `agents/canvas_agent.py` deterministic builder and `bridge/canvas_bridge.py` resolver. Add only the minimum data needed for Garden card to source relationships, then change the layout to top Theme strip, central Garden cards, and bottom native file-node evidence index.

**Tech Stack:** Python, Obsidian Canvas JSON, pytest.

---

### Task 1: Garden Card Board Layout

**Files:**
- Modify: `agents/canvas_agent.py`
- Test: `tests/test_canvas_agent.py`

- [ ] **Step 1: Write failing tests**

Replace Canvas-agent layout expectations so tests require:
- Garden cards are central `text` nodes with kind, wiki link, summary, and source refs.
- Capture notes are bottom `file` nodes with real `file` paths for reliable Obsidian opening.
- Theme is a top strip, not a center hub.
- Custom colors use light hex strings instead of dark preset-only colors.

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `.venv/bin/python -m pytest tests/test_canvas_agent.py -q`

Expected: FAIL because the current builder still makes captures the left column and Garden cards the right column.

- [ ] **Step 3: Implement minimal builder changes**

Update `CanvasItem` with `source_ids: list[str] = field(default_factory=list)`. Generate:
- top `theme` text node;
- up to 6 central Garden card text nodes;
- bottom capture `file` nodes;
- Garden-to-source edges when `source_ids` match capture ids;
- overflow text nodes for hidden Garden cards or sources.

- [ ] **Step 4: Run focused tests and confirm pass**

Run: `.venv/bin/python -m pytest tests/test_canvas_agent.py -q`

Expected: PASS.

### Task 2: Bridge Source Relationships

**Files:**
- Modify: `bridge/canvas_bridge.py`
- Test: `tests/test_canvas_bridge.py`

- [ ] **Step 1: Write failing bridge test**

Update bridge tests to assert generated Canvas has Garden card text nodes and source `file` nodes, and that a Garden card with `capture_ids=["cap1"]` links to the corresponding source file node.

- [ ] **Step 2: Run focused bridge tests and confirm failure**

Run: `.venv/bin/python -m pytest tests/test_canvas_bridge.py -q`

Expected: FAIL until bridge passes `capture_ids` into `CanvasItem.source_ids`.

- [ ] **Step 3: Implement minimal bridge change**

In `_garden_items`, pass `source_ids=_json_list(row.get("capture_ids"))`.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/python -m pytest tests/test_canvas_agent.py tests/test_canvas_bridge.py -q`

Expected: PASS.

### Task 3: Real Vault Verification

**Files:**
- No source file changes.

- [ ] **Step 1: Run Hermes tool tests**

Run: `.venv/bin/python -m pytest tests/test_canvas_agent.py tests/test_canvas_bridge.py tests/test_e2e_hermes_tools.py -q`

Expected: PASS.

- [ ] **Step 2: Regenerate real AI Agent Canvas**

Run a local `generate_canvas(theme="AI Agent")` call. Verify:
- `/Users/pipi/Obsidian/hermes-vault/canvas/ai-agent-关系图.canvas` is valid JSON;
- Garden cards are central text nodes;
- capture source nodes are native `file` nodes;
- no duplicate `-Hermes` Canvas is created.

- [ ] **Step 3: Restart Hermes**

Back up and clear `~/.hermes/sessions`, restart the gateway, and verify Weixin and Feishu are connected.
