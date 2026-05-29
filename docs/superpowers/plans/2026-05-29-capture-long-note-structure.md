# Capture Long Note Structure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make medium/long committed Capture notes easier for RAG and Review agents to inspect by adding Obsidian-friendly outline metadata and a concise `## 目录纲要` section only when the note is long enough to need it.

**Status:** Superseded for Capture Agent. The outline responsibility has been moved out of Capture and should belong to a future Library Agent maintenance pass. Capture Agent should preserve full-text save requests without compression; Library Agent should later scan long notes and add outline metadata.

**Architecture:** Keep WeChat preview unchanged. At commit time, Capture Agent asks the existing `Outliner` for a compact outline, saves that outline in Capture frontmatter, and prepends a directory section to the Markdown body if one is not already present. If outline generation fails, commit still succeeds without blocking the user.

**Tech Stack:** Python, SQLite draft/capture repos, YAML frontmatter, existing `agents.outliner.Outliner`, pytest.

---

### Task 1: Markdown Writer Outline Field

**Files:**
- Modify: `agents/markdown_writer.py`
- Test: `tests/test_markdown_writer.py`

- [ ] **Step 1: Write failing tests**

Add tests showing that capture frontmatter can store an `outline` list, while raw notes stay minimal and do not include it.

- [ ] **Step 2: Run focused tests**

Run: `.venv/bin/python -m pytest tests/test_markdown_writer.py -q`

Expected: fail because `NoteMeta` has no `outline` field and `write_note()` does not write it.

- [ ] **Step 3: Implement minimal support**

Add `outline: list[dict] = field(default_factory=list)` to `NoteMeta`. In `write_note()`, include `outline` only for non-raw notes and only when the list is non-empty.

- [ ] **Step 4: Re-run focused tests**

Run: `.venv/bin/python -m pytest tests/test_markdown_writer.py -q`

Expected: pass.

### Task 2: Capture Commit Outline Preparation

**Files:**
- Modify: `agents/capture_agent.py`
- Test: `tests/test_capture_draft_workflow.py`

- [ ] **Step 1: Write failing tests**

Add tests for four behaviors: long committed captures include outline frontmatter and `## 目录纲要`; short captures do not force outline; outliner failures do not block commit; existing `## 目录纲要` is not duplicated.

- [ ] **Step 2: Run focused tests**

Run: `.venv/bin/python -m pytest tests/test_capture_draft_workflow.py -q`

Expected: fail because `commit_draft()` does not call `Outliner` or prepare the body.

- [ ] **Step 3: Implement minimal commit-time structure**

Instantiate `Outliner` in `CaptureAgent.__init__`. Add helpers that decide whether a body is long enough, normalize outline items, render a compact directory section, and prepend it only once. In `commit_draft()`, generate outline before writing the capture note and pass it through `NoteMeta`.

- [ ] **Step 4: Re-run focused tests**

Run: `.venv/bin/python -m pytest tests/test_capture_draft_workflow.py -q`

Expected: pass.

### Task 3: Verification and Runtime Refresh

**Files:**
- No source files unless tests expose a bug.

- [ ] **Step 1: Run related tests**

Run: `.venv/bin/python -m pytest tests/test_markdown_writer.py tests/test_capture_draft_workflow.py tests/test_outliner.py -q`

Expected: pass.

- [ ] **Step 2: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`

Expected: pass.

- [ ] **Step 3: Restart Hermes with clean sessions**

Back up `/Users/pipi/.hermes/sessions`, clear that directory, restart the gateway from `/Users/pipi/Downloads/hermes-agent-main`, and verify the gateway is running from logs/state.

Expected: Hermes uses the new Capture code without stale session memory.
