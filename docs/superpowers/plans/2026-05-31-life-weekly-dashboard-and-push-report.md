# Life Weekly Dashboard And Push Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a weekly Life dashboard data view and generate a concise weekly health-expert style report capped at 600 Chinese characters.

**Architecture:** Keep the dashboard data-first: add a `/api/life/week` endpoint that returns weekly metrics, trend rows, grouped records, and weekly review status. Keep the report concise and stored in `life_weekly_reviews`; the cron reminder message uses the same concise report text. Use deterministic fact-pack/report generation first, with the current rule fallback as the P1 reliable baseline.

**Tech Stack:** Python/FastAPI/SQLite for Life backend, pytest for regression tests, Next.js/TypeScript for the dashboard.

---

### Task 1: Extend Weekly Review Storage And Report Text

**Files:**
- Modify: `db/life_schema.py`
- Modify: `db/life_repo.py`
- Modify: `agents/life_agent.py`
- Test: `tests/test_life_agent.py`
- Test: `tests/test_life_repo.py`

- [ ] Add columns to `life_weekly_reviews`: `headline`, `report_text`, `evidence_json`, `delivery_json`.
- [ ] Update schema migration to add these columns for existing databases.
- [ ] Update `LifeRepo.create_weekly_review(...)` to accept optional `headline`, `report_text`, `evidence`, and `delivery`.
- [ ] Update `_build_weekly_review(...)` so it produces a concise report under 600 Chinese characters, evidence items, and recommendations.
- [ ] Verify with focused pytest for weekly review storage and report length.

### Task 2: Add Weekly Dashboard API

**Files:**
- Modify: `api/routes/life.py`
- Test: `tests/test_life_api.py`

- [ ] Add `GET /api/life/week?date=YYYY-MM-DD`.
- [ ] Return week bounds, metrics, seven daily trend rows, grouped records, and weekly review status/headline.
- [ ] Keep empty-week response stable and data-first.
- [ ] Verify with API tests for empty and populated weeks.

### Task 3: Add Week Mode To Life Dashboard

**Files:**
- Modify: `dashboard/src/lib/lifeApi.ts`
- Modify: `dashboard/src/pages/life.tsx`

- [ ] Add TypeScript types and `fetchLifeWeek(...)`.
- [ ] Add `日视图 / 周视图` segmented control.
- [ ] Keep existing day view unchanged.
- [ ] Add weekly metric tiles, seven-day trend table, grouped record list, and report delivery status/headline.
- [ ] Verify with `cd dashboard && npx tsc --noEmit`.

### Task 4: Final Verification

**Files:**
- No new files.

- [ ] Run Life pytest subset.
- [ ] Run `python3 scripts/life_smoke.py`.
- [ ] Run `python3 scripts/life_audit.py`.
- [ ] Run dashboard TypeScript check.
- [ ] Check `git diff --check`.
