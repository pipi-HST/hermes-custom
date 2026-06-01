# Structured Garden Canvas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert Garden Canvas from a card board into a structure-first clustered map where LLM plans the knowledge structure and deterministic code renders valid Obsidian Canvas JSON.

**Architecture:** Add a small `GardenStructureAgent` that receives Garden Card metadata and returns a strict structure JSON. `agents/canvas_agent.py` renders that validated structure into Canvas nodes; `bridge/canvas_bridge.py` resolves cards/sources, calls the planner, and refuses to create a fake Canvas if no structure is found.

**Tech Stack:** Python, httpx, Obsidian Canvas JSON, pytest.

---

### Task 1: Structure Planner

**Files:**
- Create: `agents/garden_structure_agent.py`
- Test: `tests/test_garden_structure_agent.py`

- [x] **Step 1: Write failing tests**

Test that planner parses a valid `structured` JSON response, rejects unknown card ids, clamps unsupported roles, and returns `not_worth_canvas` when the LLM says no clear structure exists.

- [x] **Step 2: Implement planner**

Create a prompt that asks the LLM to decide whether Garden Cards form a structured cluster, then output only JSON with `status`, `core_claim`, `groups`, and `evidence_ids`.

- [x] **Step 3: Verify**

Run: `.venv/bin/python -m pytest tests/test_garden_structure_agent.py -q`

### Task 2: Structure Renderer

**Files:**
- Modify: `agents/canvas_agent.py`
- Test: `tests/test_canvas_agent.py`

- [x] **Step 1: Write failing tests**

Test that structured input renders a synthetic core node, role lanes, Garden Cards inside those roles, and an evidence bar; if no structure is supplied, existing fallback card-board behavior still works.

- [x] **Step 2: Implement renderer**

Add `CanvasStructure` and `CanvasStructureGroup` dataclasses. When `CanvasBuildInput.structure` exists, render structure-first layout; otherwise keep existing fallback.

- [x] **Step 3: Verify**

Run: `.venv/bin/python -m pytest tests/test_canvas_agent.py -q`

### Task 3: Bridge Integration

**Files:**
- Modify: `bridge/canvas_bridge.py`
- Test: `tests/test_canvas_bridge.py`

- [x] **Step 1: Write failing tests**

Patch the structure planner in bridge tests. Assert `generate_canvas` creates a structure Canvas when planner returns `structured`, and returns an explanatory error when planner returns `not_worth_canvas`.

- [x] **Step 2: Implement integration**

Call `GardenStructureAgent.plan()` after resolving Garden Cards. Pass structure into `CanvasBuildInput`. If status is `not_worth_canvas`, return an error envelope telling the user MOC is enough.

- [x] **Step 3: Verify**

Run: `.venv/bin/python -m pytest tests/test_canvas_agent.py tests/test_canvas_bridge.py tests/test_garden_structure_agent.py tests/test_e2e_hermes_tools.py -q`

### Task 4: Real Vault Smoke Test

**Files:**
- No source file changes.

- [x] Regenerate `/Users/pipi/Obsidian/hermes-vault/canvas/ai-agent-关系图.canvas`.
- [x] Verify JSON is valid, core node exists, role groups exist, and edge count is small and semantic.
- [x] Restart Hermes after code changes and verify Weixin/Feishu connected.
