# Life Dashboard Design

Date: 2026-05-30

## Positioning

Life Dashboard is a read-only operational dashboard for Life Assistant. Its first job is to make the reminder-record pipeline visible: what was recorded, what was calculated, what reminders ran, and whether the scheduler is healthy.

It is not a manual query tool, not a record editor, and not a new Life interaction channel. User-facing Life interactions still happen through WeChat/Hermes. The dashboard exists for inspection, debugging, and confidence.

## Goals

- Show whether Life Assistant is actually writing `life_*` data.
- Show daily sitting and sedentary metrics without reading SQLite manually.
- Show sleep, exercise, water, and meal records in a scannable timeline.
- Show reminder logs and latest tick evidence so repeated reminders or failed cron jobs are easy to diagnose.
- Reuse the existing `dashboard/` Next.js app and `api/` FastAPI backend.
- Keep Life dashboard code separate from existing knowledge/capture dashboard code.

## Non-Goals

- No manual record creation, editing, deletion, or correction in P1.
- No chat interaction inside the dashboard.
- No medical scoring or health diagnosis.
- No new standalone frontend service.
- No changes to capture, theme, garden, or knowledge-management flows.
- No Obsidian sync for Life records.

## Recommended Approach

Use the existing dashboard stack:

- Add read-only Life API routes under `/api/life`.
- Add a Next.js page at `/life`.
- Keep the existing capture timeline page intact.
- Query the existing SQLite database through Life-specific read models.

This gives the user one local dashboard app while keeping the Life surface logically isolated.

## Page Structure

The first page is a hybrid operations board.

Top area:

- Date controls: today, yesterday, previous day, next day, and 7-day quick range.
- Health metric cards:
  - accumulated sitting time
  - sedentary over-limit time
  - exercise status
  - sleep, water, and meal counts

Middle area:

- Life event timeline, sorted by `occurred_at`.
- Each row shows time, event type, short summary, duration when available, source, and status.

Right or lower area:

- Reminder and scheduler evidence:
  - latest reminder logs
  - reminder type
  - scheduled time
  - status
  - dedupe key
  - payload summary
  - latest tick freshness indicator when available

The page should favor dense but calm operational UI. It should look like a local debugging console with health context, not a marketing page.

## Metrics

Daily metrics are derived from `life_events` and `life_reminder_logs`.

Sitting metrics:

- `sitting_total_minutes`: sum of completed sitting windows for the selected day.
- `sedentary_over_limit_minutes`: sum of completed sitting windows whose duration is at least 60 minutes.
- Active sitting windows may be shown separately as "in progress" but should not be mixed into completed totals unless explicitly labeled.

Record metrics:

- sleep sessions and durations when start/end can be paired
- exercise records and scheduled exercise reminders
- water event count
- meal event count and latest meal content

Reminder metrics:

- most recent reminders by type
- statuses such as `scheduled`, `sending`, `skipped`, `paused`, `auto_resolved`, and `failed`
- repeated reminder evidence via `dedupe_key`

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
- avoid changing the existing capture page route

Visual tone:

- utilitarian, compact, local-operations style
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
- `/life` renders metric cards, timeline, and reminder evidence
- empty and error states are visible

Manual verification:

- run FastAPI backend on port 8000
- run Next.js dashboard on port 3000
- open `http://localhost:3000/life`
- compare visible records with `life_events` and `life_reminder_logs` from SQLite

## Acceptance Criteria

- The user can open `/life` and see today's Life records without touching SQLite.
- Sitting total and sedentary over-limit time are visible.
- Exercise, sleep, water, and meals are visible when recorded.
- Reminder logs are visible enough to debug duplicate or failed reminders.
- Existing dashboard home page still works.
- No write actions are exposed.
- Life code remains isolated from knowledge/capture code except for shared app shell and config loading.
