# Life Reminder Digest And Sedentary Hourly Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce Weixin/iLink rate limiting by changing Life reminders from many single messages into hourly sedentary milestones plus two daily digest messages.

**Architecture:** Keep Life event recording unchanged. Change reminder candidate generation in `agents/life_agent.py`: sedentary reminders fire at 60/120/180 minute milestones; water and exercise merge into `action_digest`; sleep and meal record prompts merge into `record_digest`. Update runtime rule labels in `api/routes/life.py` and dashboard labels in `dashboard/src/pages/life.tsx`.

**Tech Stack:** Python 3.11, SQLite, pytest, FastAPI route helpers, Next.js/TypeScript dashboard.

---

### Task 1: Sedentary Hour Milestones

**Files:**
- Modify: `agents/life_agent.py`
- Test: `tests/test_life_agent.py`

- [ ] Write tests showing sedentary fires at 60 minutes, does not repeat at 80 minutes, fires again at 120 minutes, and uses milestone dedupe keys.
- [ ] Replace repeat-interval logic in `_sedentary_candidate` with elapsed-hour milestone logic.
- [ ] Run `./.venv/bin/python -m pytest tests/test_life_agent.py -q -k 'sedentary_alert'`.

### Task 2: Action And Record Digests

**Files:**
- Modify: `agents/life_agent.py`
- Test: `tests/test_life_agent.py`

- [ ] Write tests showing 10:30 action digest combines exercise and water when both are due.
- [ ] Write tests showing 21:30 record digest combines missing meal and missing sleep duration.
- [ ] Replace single-message water/exercise/sleep-record/meal-record candidates in `life_tick` with digest candidates, while keeping sleep bedtime, daily review, weekly review, long-term review unchanged.
- [ ] Run targeted digest tests.

### Task 3: Runtime And Dashboard Labels

**Files:**
- Modify: `api/routes/life.py`
- Modify: `dashboard/src/pages/life.tsx`
- Modify: `dashboard/src/lib/lifeApi.ts` only if types need new fields
- Test: `tests/test_life_api.py`

- [ ] Update reminder labels for `action_digest` and `record_digest`.
- [ ] Update runtime rule schedule text: sedentary is hourly over-time milestones; action digest is water plus exercise; record digest is sleep plus meal补记.
- [ ] Run `./.venv/bin/python -m pytest tests/test_life_api.py -q -k 'runtime'`.
- [ ] Run `cd dashboard && npx tsc --noEmit`.

### Task 4: Full Verification

**Files:**
- No additional files.

- [ ] Run Life regression:
  `./.venv/bin/python -m pytest tests/test_life_repo.py tests/test_life_memory.py tests/test_life_intent_parser.py tests/test_life_agent.py tests/test_life_bridge.py tests/test_life_api.py tests/test_life_scenarios.py tests/test_life_smoke_script.py -q`
- [ ] Restart FastAPI on port 8000.
- [ ] Verify `/api/life/runtime` returns the new rule labels and no API error.
