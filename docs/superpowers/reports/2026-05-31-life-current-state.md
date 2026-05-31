# Life Assistant Current State - 2026-05-31

## Scope

This report covers Life Assistant only. Knowledge, Garden, Theme, and RAG changes are intentionally treated as separate workstreams.

## Current Verdict

Life Assistant is ready for trial-run acceptance, not final production closure.

P0/P1 reminder-record-review pipeline is mostly closed in code and local runtime. P2 long-term decision review has a minimal working loop. The remaining work is mainly runtime hardening, real end-to-end validation, and Life-only diff cleanup.

## Implemented

- Life data model in `life_*` tables.
- WeChat/Hermes record entry through `record_life_event`.
- Cron-driven reminder entry through `life_tick`.
- Sleep start/end/session records and duration backfill.
- Meal records with explicit/missing time handling, same-slot overwrite, and beverage supplements.
- Water records and lightweight water reminders.
- Exercise records, exercise scheduling, skip handling, and repeat throttling.
- Sedentary windows from Mac idle time, activity breaks, active sitting timer, and repeat Sedentary Alerts.
- Daily Light Review persistence.
- Weekly Deep Review persistence in tests.
- Long-term decision record, required user-specified review time, review reminder, review reply, and close flow.
- Read-only Life API and `/life` dashboard.
- Runtime audit command: `python3 scripts/life_audit.py`.

## Not Implemented

- Reflection / thought-progress review.
- Rich health-expert coaching beyond lightweight daily/weekly summaries.
- Manual Life status query.
- Dashboard editing, deletion, manual correction, or record creation.
- Apple Health, phone, watch, or wearable data sync.
- Nutrition, calories, macros, or medical analysis.
- Deterministic Hermes pre-router. Routing still depends on the Hermes LLM choosing the right tool, guarded by prompt and tool descriptions.

## Runtime Evidence

- Life regression suite: `126 passed, 42 deselected`.
- Life smoke script tests: `6 passed`.
- API `/api/life/reviews` returns active long-term decisions.
- Live DB currently has Life events for meals, water, sleep, sedentary windows, and long-term decisions.
- Live DB currently has 1 daily review and 0 weekly reviews.
- Live DB currently has 2 active long-term decisions and 0 pending long-term decisions waiting for review time.
- Runtime audit status is `warning`, because historical failed reminder logs exist.

## Current Gaps

- Historical reminder failures remain in `life_reminder_logs`: 16 total failed logs at the time of this audit.
- Weekly review still needs one true live scheduled validation.
- Real user-completed exercise samples are still thin compared with reminders and plans.
- Worktree contains mixed Garden/Theme/RAG changes. Life changes must be isolated before staging or merging.
- The Life dashboard is read-only, so data correction still depends on WeChat flows or direct developer intervention.

## Life-Only Diff Boundary

Life-owned files include:

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

Shared files touched by Life and other workstreams need careful review:

- `api/main.py`
- `dashboard/package.json`
- `db/schema.py`
- `tests/test_e2e_hermes_tools.py`
- `/Users/pipi/.hermes/config.yaml`
- `/Users/pipi/Downloads/hermes-agent-main/tools/record_life_event_tool.py`

Non-Life dirty files should not be staged with Life:

- Garden files and tests.
- Theme files and tests.
- RAG files and tests.
- Agent run ledger files and tests.

## Manual E2E Acceptance Checklist

Run these through WeChat/Hermes, then compare `/life`, `/api/life/*`, and `python3 scripts/life_audit.py`.

1. Sleep: `我睡了`, then later `我醒了`.
2. Sleep with explicit time: `今天凌晨三点半睡的`, `中午13点半醒的`.
3. Meal without time: `午饭吃了面包`; expected: record content, ask or mark missing meal time without using chat time as meal time.
4. Meal supplement: `中午还喝了茉莉乌龙茶`; expected: meal beverage supplement, not water.
5. Water: `喝了一杯水`; expected: water event.
6. Exercise schedule: `我打算晚上8点练八段锦`; expected: scheduled exercise at 20:00.
7. Exercise completion: `已运动`; expected: stop today's exercise reminders.
8. Sedentary: leave Mac idle for more than 5 minutes; expected: active sitting window resolves.
9. Long-term decision: `长期决定：未来三个月优先打磨 Life Assistant`; expected: asks for next review time.
10. Long-term review time: `6月15日晚上8点复盘`; expected: decision becomes active with that time.

## Next Moves

1. Run the manual E2E checklist once and save the exact observed replies.
2. Add a small recurring audit habit around `python3 scripts/life_audit.py`.
3. Clear or classify historical failed reminder logs only after confirming they are old and harmless.
4. Wait for or simulate the next weekly review, then mark weekly live validation closed.
5. Split Life changes from Garden/Theme/RAG before staging or committing.
