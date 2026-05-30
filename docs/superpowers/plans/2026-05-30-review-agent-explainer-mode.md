# Review Agent Explainer Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change Review Agent from quiz-style recall prompts to one detailed daily knowledge explanation with per-topic and per-capture view counts.

**Architecture:** Keep the existing `review_items` table as the scheduling unit, but reinterpret each row as a review topic instead of a test question. `ReviewAgent.run_daily()` selects one due topic, increments topic/material counters, and the bridge formats a WeChat-friendly explanation preview. Hermes keeps the same `review_feedback` tool but changes user-facing outcomes to "useful / revisit / mute / deeper".

**Tech Stack:** Python, SQLite, pytest, Hermes Agent Result Envelope, Hermes tool registry.

---

### Task 1: Review Topic Fields And Migration

**Files:**
- Modify: `/Users/pipi/hermes-custom/db/schema.py`
- Test: `/Users/pipi/hermes-custom/tests/test_review_agent.py`

- [ ] Add failing tests that expect `review_items.view_count` and `review_items.last_viewed` to exist after `init_db()`.
- [ ] Update `CREATE_REVIEW_ITEMS_TABLE` and add a tiny migration that adds missing columns for existing databases.
- [ ] Run `pytest tests/test_review_agent.py tests/test_schema.py -q`.

### Task 2: Daily Explanation Selection

**Files:**
- Modify: `/Users/pipi/hermes-custom/agents/review_agent.py`
- Test: `/Users/pipi/hermes-custom/tests/test_review_agent.py`

- [ ] Add failing tests that `run_daily()` returns exactly one `review_explanation` item for due captures.
- [ ] Add failing tests that each delivered item increments both `review_items.view_count` and `captures.review_count`.
- [ ] Replace quiz copy with explanation copy: `topic`, `explanation`, `why_revisit`, `source_path`, `view_count`, and `capture_view_count`.
- [ ] Keep section-based long-note splitting from the previous implementation.

### Task 3: Bridge Preview

**Files:**
- Modify: `/Users/pipi/hermes-custom/bridge/review_bridge.py`
- Test: `/Users/pipi/hermes-custom/tests/test_review_agent.py`

- [ ] Add failing test for preview text containing `今日回顾`, source title/path, topic count, material count, and follow-up suggestions.
- [ ] Format one detailed explanation instead of three quiz questions.
- [ ] Preserve `meta.review_item_ids` for feedback and continuity.

### Task 4: Feedback Tool Semantics

**Files:**
- Modify: `/Users/pipi/hermes-custom/agents/review_agent.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/review_feedback_tool.py`
- Test: `/Users/pipi/hermes-custom/tests/test_review_agent.py`
- Test: `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_review_feedback_tool.py`

- [ ] Accept outcomes `useful`, `revisit`, `mute`, and `deeper`.
- [ ] Map old outcomes for compatibility: `remembered -> useful`, `forgotten -> revisit`, `skip -> mute`, `mastered -> mute`.
- [ ] Update tool description and enum to match the new interaction language.

### Task 5: Verification And Runtime

**Files:**
- No new files.

- [ ] Run `/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/ -q`.
- [ ] Run `/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' tests/tools/test_review_feedback_tool.py tests/test_toolsets.py -q`.
- [ ] Commit and push both repos.
- [ ] Back up `/Users/pipi/.hermes/sessions`, clear it, restart Hermes, and verify gateway state plus Weixin/Feishu connectivity.
