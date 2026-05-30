# Life Dashboard Design

Date: 2026-05-30

## Positioning

Life Dashboard is a read-only personal activity log for Life Assistant. Its first job is to make the user's real-life records visible: sitting windows, sleep, meals, water, exercise plans, and exercise completions.

It is not an Agent activity log. Reminder attempts, dedupe keys, tick status, and other scheduler evidence are diagnostic details, not the main product surface.

It is also not a manual query tool, record editor, or new Life interaction channel. User-facing Life interactions still happen through WeChat/Hermes. The dashboard exists for inspection, confidence, and lightweight lifestyle review.

## Goals

- Show detailed personal activity records without reading SQLite manually.
- Show daily sitting and sedentary metrics, including each sitting window.
- Show the active sitting window as a live timer that updates at least once per second.
- Show sleep, exercise, water, and meal records with useful detail, not just counts.
- Keep scheduler diagnostics available but visually secondary and collapsed by default.
- Reuse the existing `dashboard/` Next.js app and `api/` FastAPI backend.
- Keep Life dashboard code separate from existing knowledge/capture dashboard code.

## Non-Goals

- No manual record creation, editing, deletion, or correction in P1.
- No chat interaction inside the dashboard.
- No medical scoring or health diagnosis.
- No new standalone frontend service.
- No changes to capture, theme, garden, or knowledge-management flows.
- No Obsidian sync for Life records.
- No Agent-log-first UI. The primary surface should be about the user's activity, not tool calls.

## Recommended Approach

Use the existing dashboard stack:

- Add read-only Life API routes under `/api/life`.
- Add a Next.js page at `/life`.
- Keep the existing capture timeline page intact.
- Query the existing SQLite database through Life-specific read models.

This gives the user one local dashboard app while keeping the Life surface logically isolated.

## Page Structure

The first page is a module-based personal activity log.

Top area:

- Date controls: today, yesterday, previous day, next day, and 7-day quick range.
- Compact daily summary:
  - accumulated sitting time
  - sedentary over-limit time
  - active sitting status
  - sleep, water, meal, and exercise presence

Middle area:

- Module sections:
  - sitting records
  - sleep records
  - meal records
  - water records
  - exercise records

Sitting module:

- Show the active sitting window prominently when present.
- The active timer should update on the client every second and display hours, minutes, and seconds.
- Show each completed sitting window as a row with start time, end time, duration, and whether it crossed the sedentary threshold.
- Show total sitting time and sedentary over-limit time near the active timer.

Sleep module:

- Show individual sleep start and wake records.
- Show paired sessions when possible.
- Show unpaired records explicitly instead of hiding them behind a count.

Meal, water, and exercise modules:

- Show each record with time and content.
- Meal rows show meal slot and content.
- Water rows show the original water record and time.
- Exercise rows show completed exercise records and scheduled exercise plans separately when available.

Lower area:

- A collapsed diagnostic section for reminder logs and scheduler evidence.
- This section may show reminder type, scheduled time, status, dedupe key, payload, and errors, but it must not be the default reading experience.

The page should favor dense but readable personal logging. It should feel like a lifestyle ledger with a real-time sitting panel, not a local debugging console and not a marketing page.

## Metrics

Daily metrics are derived from `life_events` and `life_reminder_logs`.

Sitting metrics:

- `sitting_total_minutes`: sum of completed sitting windows for the selected day.
- `sedentary_over_limit_minutes`: sum of completed sitting windows whose duration is at least 60 minutes.
- `active_sitting_elapsed_seconds`: derived in the browser from active sitting `occurred_at` and current time.
- The UI may show a real-time displayed total that adds active elapsed time to completed sitting time, but the label must make this clear.

Record metrics:

- sleep sessions and durations when start/end can be paired
- exercise records and scheduled exercise reminders
- water event count
- meal event count and latest meal content

Reminder metrics:

- most recent reminders by type
- statuses such as `scheduled`, `sending`, `skipped`, `paused`, `auto_resolved`, and `failed`
- repeated reminder evidence via `dedupe_key`
- reminder metrics are diagnostic-only in the main page.

## API Design

Add `api/routes/life.py`.

Endpoints:

- `GET /api/life/summary?date=YYYY-MM-DD`
  - Returns daily metrics, active sitting status, record counts, and latest reminder status.
- `GET /api/life/events?date=YYYY-MM-DD&limit=100`
  - Returns Life events for the selected day.
- `GET /api/life/reminders?date=YYYY-MM-DD&limit=100`
  - Returns reminder logs for the selected day.

The API should be read-only and should not expose write operations.

## Frontend Design

Add `dashboard/src/pages/life.tsx`.

Add Life API helpers in `dashboard/src/lib/api.ts` or a Life-specific `dashboard/src/lib/lifeApi.ts`.

The page should:

- fetch summary, events, and reminders on load
- default to today
- show loading and empty states
- show API failure as an inline diagnostic state
- preserve readable Chinese labels
- group visible content by personal activity module
- render the active sitting timer with client-side per-second updates
- keep reminder diagnostics collapsed by default
- avoid changing the existing capture page route

Visual tone:

- personal ledger, compact, readable
- clear metric hierarchy
- high information density without decorative hero sections
- no nested cards inside cards

## Data Boundaries

Life Dashboard reads only `life_*` tables.

It must not query or mutate:

- `captures`
- capture drafts
- theme pages
- garden data
- Obsidian files

The only shared resource is the SQLite connection path from existing config.

## Testing

Backend tests:

- summary endpoint returns derived sitting and sedentary metrics
- events endpoint returns only selected-day Life events
- reminders endpoint returns selected-day reminder logs
- empty database returns empty but valid structures

Frontend checks:

- dashboard builds successfully
- `/life` renders module sections for sitting, sleep, meals, water, and exercise
- active sitting timer renders from active sitting data
- reminder diagnostics are present but collapsed by default
- empty and error states are visible

Manual verification:

- run FastAPI backend on port 8000
- run Next.js dashboard on port 3000
- open `http://localhost:3000/life`
- compare visible records with `life_events` and `life_reminder_logs` from SQLite

## Acceptance Criteria

- The user can open `/life` and see today's Life records without touching SQLite.
- Sitting total, sedentary over-limit time, and current active sitting timer are visible.
- Each completed sitting window is visible as its own row.
- Exercise, sleep, water, and meals are visible with record details when recorded.
- Reminder logs are available in a collapsed diagnostic section.
- Existing dashboard home page still works.
- No write actions are exposed.
- Life code remains isolated from knowledge/capture code except for shared app shell and config loading.
