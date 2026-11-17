---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Garden Duplicate Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Garden Agent from repeatedly creating new cards for the same knowledge object.

**Architecture:** Add a small deterministic duplicate detector after `GardenAgent.synthesize()` and before `create_garden_draft()`. The detector compares the proposed card with committed `garden_cards` and open `garden_drafts` using normalized text, card kind, and source overlap. Exact/high-confidence duplicates are blocked with a user-visible suggestion to update the existing card; possible duplicates still create a draft but show a warning.

**Tech Stack:** Python, SQLite, existing Garden bridge/envelope, pytest.

---

## Scope

V1 does not merge cards automatically. It only:

1. Blocks clear duplicates.
2. Warns on possible duplicates.
3. Leaves explicit merge/update as a later task.

## Files

- Create: `agents/garden_duplicates.py`
- Modify: `bridge/garden_bridge.py`
- Test: `tests/test_garden_duplicates.py`
- Test: `tests/test_garden_bridge.py`

## Duplicate Rules

- `duplicate`: same `card_kind`, high text similarity, and meaningful source overlap; or identical normalized `one_liner`.
- `possible_duplicate`: high text similarity or high source overlap, but not enough to block.
- `none`: no close match.

Compare against:

- committed `garden_cards`
- open `garden_drafts` with status `draft_ready` or `revision_requested`

## Tasks

### Task 1: Add Duplicate Detector

- [ ] Create `agents/garden_duplicates.py` with `find_garden_duplicate(conn, candidate)`.
- [ ] Add tests for exact duplicate, possible duplicate, and no match.
- [ ] Verify: `.venv/bin/python -m pytest tests/test_garden_duplicates.py -q`

### Task 2: Guard Garden Synthesis

- [ ] In `_synthesize_garden()`, call detector after `GardenAgent.synthesize()`.
- [ ] If `duplicate`, return success envelope without creating `garden_drafts`.
- [ ] If `possible_duplicate`, create draft as today but add warning and meta.
- [ ] Add bridge tests for blocked duplicate and warning duplicate.
- [ ] Verify: `.venv/bin/python -m pytest tests/test_garden_bridge.py tests/test_garden_duplicates.py -q`

### Task 3: Runtime Verification

- [ ] Run focused tests.
- [ ] Restart Hermes after code changes and clear session memory.
- [ ] User smoke test: ask Garden to synthesize the same known group twice. Expected second run should not create a second Garden draft/card when duplicate is clear.
