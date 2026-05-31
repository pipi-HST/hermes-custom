# Life Assistant Closeout Audit - 2026-05-31

## Scope

This report covers Life Assistant only. Knowledge, Garden, Theme, and RAG files are intentionally treated as separate workstreams and should not be staged with Life changes.

## Verdict

Life is ready for continued trial use. The core record -> reminder -> delivery writeback -> review -> API -> dashboard loop is working locally, and the audit command now catches the two data-quality failures that were painful in manual testing: duplicate sleep sessions and reminder bursts.

The live database still has historical reminder failures and historical reminder bursts from earlier bugs or Weixin delivery limits. They are now visible in `scripts/life_audit.py` instead of being hidden in the database.

## Work Completed In This Closeout

- Repaired live sleep data after taking a backup.
- Added audit checks for duplicate sleep sessions.
- Added audit checks for successful/scheduled reminder bursts.
- Verified API and dashboard HTTP availability.
- Re-ran the Life regression suite and smoke command.

## Data Repairs

Backup created before mutation:

- `data/captures.db.before-life-closeout-repair-20260531-211434.bak`

Repairs applied:

- `8487b28ffb97` sleep_end now has `duration_minutes=600` for the 2026-05-30 03:30 -> 13:30 sleep session.
- `8015ca1b2344` sleep_start raw text was aligned to the same session wording.
- Duplicate 2026-05-31 03:30 -> 13:30 sleep rows were deduped by marking the older pair as `superseded`:
  - `94515aa2b046` superseded by `d0541bcb7eeb`
  - `e3b61ca83ff2` superseded by `343c117625f5`

Post-repair check:

- Duplicate active sleep sessions: `0`
- Sleep minutes by day:
  - 2026-05-30: `600`
  - 2026-05-31: `601` including one 1-minute manual test sleep record.

## Current Runtime Evidence

Commands:

- `python3 -m pytest tests/test_life_agent.py tests/test_life_intent_parser.py tests/test_life_repo.py tests/test_life_api.py tests/test_life_bridge.py tests/test_life_smoke_script.py tests/test_life_scenarios.py -q`
- `python3 scripts/life_smoke.py`
- `python3 scripts/life_audit.py`
- `curl http://localhost:8000/api/life/summary?date=2026-05-31`
- `curl http://localhost:8000/api/life/events?date=2026-05-31`
- `curl http://localhost:8000/api/life/reviews?date=2026-05-31`
- `curl http://localhost:3010/life`

Results:

- Life regression suite: `138 passed, 1 warning`
- Smoke: `passed`, `28` checks, `0` failed
- API summary/events/reviews: HTTP `200`
- Dashboard `/life`: HTTP `200`
- Audit status: `warning`

Audit warning reasons:

- `failed_reminders=16`, all observed failures are historical Weixin delivery/rate-limit failures.
- `reminder_bursts=20`, historical examples include exercise one-minute bursts around 2026-05-31 14:51-14:56 and earlier sedentary short gaps.
- `duplicate_sleep_sessions=0`
- `sending_reminders=0`
- `pending_long_term_decisions=0`

## Current Live Data Notes

- Active sitting window exists and is expected while the Mac remains active.
- Recent sedentary reminders are spaced at roughly 10 minutes after the current fix.
- There are still two `exercise` events with `completed_unknown`; they are personal activity records from manual testing and can be completed by replying with exercise type and duration, or cleaned later if you decide they are test noise.
- There is one 1-minute sleep start/end pair from manual E2E testing. It is not a pipeline failure, but it may make the personal log feel noisy.
- Weekly review has code and smoke coverage, but live weekly natural scheduling is still pending.

## Life-Only Diff Boundary

Life-owned files touched or created:

- `agents/life_agent.py`
- `agents/life_intent_parser.py`
- `api/routes/life.py`
- `bridge/life_bridge.py`
- `db/life_repo.py`
- `db/life_schema.py`
- `scripts/life_smoke.py`
- `scripts/life_tick_cron.py`
- `scripts/life_audit.py`
- `dashboard/src/lib/lifeApi.ts`
- `dashboard/src/pages/life.tsx`
- `tests/test_life_*.py`
- `docs/superpowers/reports/2026-05-31-life-closeout-audit.md`

Shared files that need careful staging:

- `api/main.py`
- `dashboard/package.json`
- `tests/test_e2e_hermes_tools.py`

Non-Life dirty files that should not be staged with Life:

- `agents/garden_agent.py`
- `agents/theme_agent.py`
- `agents/theme_graph.py`
- `bridge/librarian_bridge.py`
- `bridge/review_bridge.py`
- `bridge/theme_bridge.py`
- `tests/test_garden_agent.py`
- `tests/test_theme_agent.py`
- `tests/test_theme_bridge.py`
- `tests/test_theme_graph.py`
- Theme/Garden plan docs

## Recommended Next Steps

1. Keep using Life normally for one day and run `python3 scripts/life_audit.py` after reminders fire.
2. Decide whether to clean manual-test personal records:
   - two `completed_unknown` exercise rows
   - one 1-minute sleep pair
3. Wait for or simulate the weekly review live trigger, then mark weekly live validation closed.
4. Before staging or committing, stage Life-owned files only and leave Garden/Theme/RAG dirty files alone.
