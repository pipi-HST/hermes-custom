# Life Assistant Design

Date: 2026-05-29

## Positioning

The Life Assistant is an independent Hermes agent domain for personal life rhythm and growth review. It uses the existing WeChat/Hermes conversation entry point to remind, record, and review daily behavior.

The first version focuses on body rhythm: sedentary breaks, water, exercise, sleep, meals, and lightweight reflection. It also keeps a thin path for long-term decisions, but it does not become a full decision-management system in the first release.

The assistant is a lifestyle and reflection helper. It is not a medical diagnosis tool and must not make disease claims, medication decisions, or treatment recommendations.

## Goals

- Remind the user about sedentary breaks, water, exercise, and sleep through WeChat/Hermes.
- Let the user record sleep, meals, exercise, water, sedentary breaks, reflections, and long-term decisions in natural language.
- Calculate useful derived values, especially sleep duration, exercise duration, sedentary duration, reminder completion, and skipped reminders.
- Run a lightweight daily review at midnight.
- Run a deeper weekly lifestyle review on Sunday evening.
- Keep life-assistant data isolated from the existing knowledge-management `captures` domain.

## Non-Goals

- No medical diagnosis, disease judgment, medication advice, or treatment planning.
- No Apple Health, watch, phone, or wearable data sync in the first version.
- No precise calorie, macro, or nutrition calculation.
- No standalone app and no Mac local notifications in the first version.
- No heavy long-term decision system in the first version.
- No changes to the existing knowledge-management capture workflow unless needed for shared Hermes routing.

## Assumptions

- WeChat/Hermes is the main user-facing channel.
- Mac activity data can be queried by a local activity source that reports active and inactive periods.
- SQLite remains the first-version storage layer.
- Life data should be queryable as structured time-series events, so raw life records should not be stored primarily as Markdown.
- Important weekly reviews, reflections, or decisions may later be selectively synced to Obsidian, but that is optional.

## Architecture

The recommended implementation is Scheme A: add the Life Assistant as a separate Hermes agent domain inside `hermes-custom`.

High-level components:

- `life_agent`: parses user messages, records life events, handles reminder replies, and generates reviews.
- `life_repo`: reads and writes the `life_*` tables.
- `life_scheduler`: evaluates due reminders and review jobs.
- `mac_activity_source`: reads Mac active and inactive windows for sedentary detection.
- `life_bridge`: exposes Hermes tool functions for recording, reminder checks, and reviews.

This keeps the feature close to Hermes routing and WeChat delivery while isolating its data and behavior from the knowledge-management agents.

## Data Model

Life Assistant data should live in independent `life_*` tables. The first version can keep these tables in the same SQLite database file as the current Hermes custom data, but the table namespace must stay separate.

### `life_events`

The canonical event stream for life records.

Useful event types:

- `sleep_start`
- `sleep_end`
- `meal`
- `exercise`
- `water`
- `sedentary_start`
- `sedentary_break`
- `reflection`
- `life_note`
- `decision`

Important fields:

- `id`
- `event_type`
- `occurred_at`
- `ended_at`
- `duration_minutes`
- `source`
- `raw_text`
- `payload_json`
- `created_at`

`payload_json` carries type-specific fields, such as meal content, exercise type, reminder id, decision rationale, or reflection text.

### `life_reminder_rules`

Stores reminder configuration.

Important fields:

- `id`
- `rule_type`
- `enabled`
- `schedule_json`
- `threshold_json`
- `created_at`
- `updated_at`

### `life_reminder_logs`

Stores each reminder attempt and outcome.

Important fields:

- `id`
- `rule_id`
- `reminder_type`
- `scheduled_for`
- `sent_at`
- `status`
- `user_response`
- `resolved_at`
- `payload_json`

Expected statuses include `scheduled`, `sent`, `confirmed`, `snoozed`, `skipped`, `auto_resolved`, and `expired`.

### `life_daily_reviews`

Stores lightweight daily reviews.

Important fields:

- `id`
- `review_date`
- `summary`
- `metrics_json`
- `questions_json`
- `created_at`

### `life_weekly_reviews`

Stores weekly lifestyle reviews.

Important fields:

- `id`
- `week_start`
- `week_end`
- `summary`
- `scores_json`
- `patterns_json`
- `recommendations_json`
- `created_at`

### `life_decisions`

Stores lightweight long-term decisions.

Important fields:

- `id`
- `title`
- `decision_text`
- `rationale`
- `expected_outcome`
- `review_on`
- `status`
- `created_at`
- `updated_at`

## Reminder Rules

### Sedentary Strong Reminder

Sedentary reminders are stateful and stronger than ordinary reminders.

Defaults:

- Trigger after 60 minutes of continuous Mac activity.
- Once triggered, send a WeChat/Hermes reminder every 10 minutes.
- Auto-resolve when Mac activity is interrupted for more than 5 minutes.
- Also resolve if the user replies that they have moved or taken a break.

State flow:

1. Mac activity remains continuous for 60 minutes.
2. Hermes enters `sedentary_active`.
3. Hermes sends reminders every 10 minutes.
4. If Mac inactivity exceeds 5 minutes, Hermes records `sedentary_break` and auto-resolves the reminder state.
5. If the user replies `已活动` or an equivalent phrase, Hermes records `sedentary_break` and resolves the reminder state.

The system should record the sedentary start time, reminder times, resolution time, total sedentary duration, and resolution source.

### Water Reminder

Defaults:

- Two daytime reminders.
- Send at 10:00 and 16:00 by default, about 6 hours apart.
- Lightweight reminder only.
- No required interaction.
- If the user replies that they drank water, record a `water` event.

### Exercise Reminder

Defaults:

- First reminder at 10:30 every day.
- If the user does not respond and has not recorded exercise, remind every 2 hours.
- Stop for the day when the user records exercise.
- Stop for the day when the user says they will not exercise today.
- If the user sets a specific exercise time, schedule the next reminder for that time.

The reminder is interactive. Expected replies include completion, snooze, a specific future time, or skip for today.

### Sleep Reminder

Defaults:

- Send at 23:30 by default.
- Lightweight reminder only.
- No required interaction.
- No repeated reminder loop.

The bedtime target can be changed later, but 23:30 is the first-version default.

### Daily Review

Defaults:

- Runs at 00:00.
- Keeps the review lightweight.
- Asks one or two questions.
- Summarizes the day across sleep, exercise, sedentary time, meals, water, and reflection if data exists.

### Weekly Review

Defaults:

- Runs Sunday at 21:30.
- Produces a deeper lifestyle review.
- Covers sleep regularity, exercise consistency, sedentary burden, meal timing/content notes, water reminder effectiveness, reflection themes, and next-week focus.

## Conversation Strategy

The first version should use a mixed parsing strategy:

- Clearly structured messages are parsed directly into structured events.
- Ambiguous messages are stored as `life_note`.
- The assistant only asks a follow-up when the ambiguity affects reminder state.

Examples:

- `我睡了` -> `sleep_start`
- `我醒了` -> `sleep_end`, calculate duration from the latest unmatched `sleep_start`
- `昨晚 1 点睡，今天 8 点醒` -> sleep session with explicit start and end
- `午饭吃了牛肉饭` -> `meal`
- `跑步 30 分钟` -> `exercise`
- `喝了` -> `water` if the active context is a water reminder
- `已活动` -> resolve active sedentary reminder and record `sedentary_break`
- `今天不练了` -> skip today's exercise reminder
- `下午 4 点提醒我运动` -> schedule exercise reminder for that time
- `今天有个反思...` -> `reflection`
- `记录一个长期决定：...` -> `decision`

## Review Behavior

Daily review should be concise and low-friction. It should not try to produce a full report every night.

Weekly review should be more analytical. It should identify patterns and propose small adjustments, while staying away from medical diagnosis.

Useful weekly review sections:

- Rhythm summary
- Sleep pattern
- Exercise consistency
- Sedentary risk
- Meal observations
- Water reminders
- Reflection and thought progress
- Suggested focus for next week

## Safety Boundaries

The assistant may say:

- Sleep has been short or irregular.
- Sedentary time is high.
- Exercise consistency is low.
- Meal timing looks late or irregular.
- A lifestyle adjustment may be worth trying.

The assistant must not say:

- The user has a specific disease.
- The user should start, stop, or change medication.
- A symptom is medically safe to ignore.
- A plan replaces a clinician's advice.

For severe or persistent symptoms, the assistant should recommend seeking qualified medical help.

## Success Criteria

- The user can record sleep start and end from WeChat, and Hermes calculates sleep duration.
- The user can record meals, exercise, water, reflections, and lightweight decisions from WeChat.
- Sedentary strong reminders repeat every 10 minutes after 60 minutes of continuous Mac activity and stop after more than 5 minutes of inactivity.
- Exercise reminders begin at 10:30, repeat every 2 hours when unresolved, and stop after exercise is recorded or skipped for the day.
- Water reminders occur twice during the day and do not require interaction.
- Sleep reminders are lightweight and non-interactive.
- Daily reviews run at 00:00.
- Weekly reviews run Sunday at 21:30.
- Life Assistant data is stored in `life_*` tables and does not pollute existing knowledge-management tables.

## Open Implementation Choices

- `mac_activity_source` must expose active and inactive windows; the exact macOS API or command can be selected during implementation.
- Daily and weekly reviews should be stored in SQLite first. Obsidian export can be added later unless implementation reveals that Markdown export is required for Hermes review.
- Reminder rules should start from code defaults. YAML editing can be added later if the defaults become annoying to change.
