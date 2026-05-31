# Life Assistant Pipeline Audit - 2026-05-31

## Scope

This audit covers Life Assistant only: record ingestion, reminders, delivery reconciliation, review persistence, API, dashboard visibility, cron runtime, and Hermes routing boundaries. Knowledge/Garden changes are intentionally out of scope.

## Verdict

Life P0/P1 pipeline is mostly closed in the local runtime. Daily review is now visible through API and dashboard. Weekly review is implemented and tested, but has not yet been verified by a real scheduled Sunday 21:30 run.

## Checklist

| Area | Status | Evidence | Gap |
| --- | --- | --- | --- |
| Record ingestion | Closed for core event types | Automated Life suite passed; smoke checked sleep, meal overwrite, water, exercise, sedentary pause/break, manual query rejection. Live DB has meal, water, sleep, sedentary rows. | Live DB currently has no user-completed exercise event, only exercise reminders/plans. |
| Reminder selection | Closed | Live `life_reminder_logs` has `sleep`, `sedentary`, `exercise`, `daily_review`; cron continues one tick per minute. Unit tests cover due windows and repeat throttling. | None observed in latest run. |
| Delivery writeback | Closed | Recent live reminders moved to `sent`; `life-assistant-tick` has `last_status=ok` and `last_delivery_error=null`. | Delivery timestamps are batch reconciled from the previous cron job completion time, which is expected for the current bridge design. |
| Daily review persistence | Closed | Live DB has `life_daily_reviews.review_date=2026-05-30` with summary `睡眠未记录；运动未记录；久坐6小时55分钟；饮食记录1条；喝水1次。`. | None. |
| Weekly review persistence | Test-closed, live pending | Unit/smoke tests create and verify `life_weekly_reviews`; smoke checked `weekly_review_rows=1`. | Real weekly trigger has not occurred yet; next natural validation is Sunday 21:30 local time. |
| API | Closed | Live HTTP 200 for `/api/life/summary`, `/api/life/events`, `/api/life/reminders`, `/api/life/reviews`. | None. |
| Dashboard | Closed for daily review visibility | Browser check on `http://localhost:3000/life` showed `定期复盘` and the 2026-05-30 daily review summary after selecting the previous day. | Weekly block will display once live weekly review exists. |
| Cron live runtime | Closed | `life-assistant-tick` completed 159 runs, latest checked run `2026-05-31T01:08:38+08:00` was `ok`; cron output was silent and `data/life.log` appended an idle tick. | None observed. |
| Hermes routing boundary | Improved, config-closed | Runtime prompt now gives Life routing priority over `capture_content` for clear lifestyle records, including voice transcriptions. Guard test passed. | True LLM routing still needs a real message spot-check after restart because the final decision is prompt/model-driven. |

## Commands Run

- `python3 -m pytest tests/test_life_api.py -q`
- `python3 -m pytest tests/test_e2e_hermes_tools.py -q -k "runtime_prompt_prioritizes_life_voice_records_over_capture"`
- `python3 -m pytest tests/test_life_api.py tests/test_life_smoke_script.py tests/test_life_scenarios.py tests/test_life_intent_parser.py tests/test_life_agent.py tests/test_life_bridge.py tests/test_life_repo.py tests/test_e2e_hermes_tools.py -q -k "life or runtime_prompt"`
- `python3 scripts/life_smoke.py --db /tmp/life-smoke-acceptance.db`
- `cd dashboard && npx tsc --noEmit`
- Live curl checks for all four Life API endpoints.
- Browser verification against `http://localhost:3000/life`.

## Runtime Changes

- Updated `/Users/pipi/.hermes/config.yaml` so Life voice transcriptions are routed by content, with Life records prioritized over `capture_content`.
- Backed up that config to `/Users/pipi/.hermes/config.yaml.bak.20260531T0105-life-route`.
- Backed up Hermes sessions to `/Users/pipi/.hermes/session_backups/20260531T0106-life-route/sessions`.
- Cleared live Hermes sessions and restarted Hermes gateway; new gateway PID observed after restart.
