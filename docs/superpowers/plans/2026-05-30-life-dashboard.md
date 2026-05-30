# Life Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `/life` dashboard that shows Life Assistant daily metrics, records, and reminder evidence.

**Architecture:** Add a Life-only FastAPI route module that reads `life_*` tables from the existing SQLite database. Add a Life-only Next.js page and API helper that render summary cards, event timeline, and reminder logs without changing the existing capture timeline.

**Tech Stack:** FastAPI, SQLite, pytest, Next.js Pages Router, React, Tailwind CSS.

---

### Task 1: Life API

**Files:**
- Create: `api/routes/life.py`
- Modify: `api/main.py`
- Test: `tests/test_life_api.py`

- [ ] Write failing API tests for daily summary, events, reminders, and empty state.
- [ ] Run `python3 -m pytest tests/test_life_api.py -q` and confirm it fails because the route does not exist.
- [ ] Implement read-only `/api/life/summary`, `/api/life/events`, and `/api/life/reminders`.
- [ ] Wire the Life router into `api/main.py`.
- [ ] Run `python3 -m pytest tests/test_life_api.py -q` and confirm it passes.

### Task 2: Life Frontend

**Files:**
- Create: `dashboard/src/pages/life.tsx`
- Create: `dashboard/src/lib/lifeApi.ts`

- [ ] Implement typed Life API helpers.
- [ ] Implement `/life` page with Chinese labels, metric cards, timeline, reminder log panel, loading state, empty state, and error state.
- [ ] Run `npm run build` in `dashboard/` and fix any type/build errors.

### Task 3: Verification and Runtime

**Files:**
- No new files expected.

- [ ] Run Life backend tests and existing Life regression tests.
- [ ] Run dashboard build.
- [ ] Start FastAPI backend on port 8000.
- [ ] Start dashboard dev server on port 3000.
- [ ] Verify `/life` is reachable and renders using local browser automation or an HTTP smoke check.
- [ ] Commit only Life Dashboard files.
