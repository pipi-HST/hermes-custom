# Life Assistant Design

Date: 2026-05-29

## Positioning

The Life Assistant is an independent Hermes agent domain for personal life rhythm. It uses the existing WeChat/Hermes conversation entry point to remind, record, and review daily behavior.

The first version focuses on body rhythm: sedentary breaks, water, exercise, sleep, and meals. Reflection and long-term decisions are deferred to P2.

The assistant is a lifestyle rhythm helper. It is not a medical diagnosis tool and must not make disease claims, medication decisions, or treatment recommendations.

## Goals

- Remind the user about sedentary breaks, water, exercise, and sleep through WeChat/Hermes.
- Let the user record sleep, meals, exercise, water, and sedentary breaks with supported life-rhythm phrases.
- Calculate useful derived values, especially sleep duration, exercise duration, sedentary duration, reminder completion, and skipped reminders.
- Run a lightweight daily review at midnight.
- Run a deeper weekly lifestyle review on Sunday evening.
- Keep life-assistant data isolated from the existing knowledge-management `captures` domain.

## Non-Goals

- No medical diagnosis, disease judgment, medication advice, or treatment planning.
- No Apple Health, watch, phone, or wearable data sync in the first version.
- No precise calorie, macro, or nutrition calculation.
- No standalone app and no Mac local notifications in the first version.
- No Reflection support in the first version; defer it to P2.
- No Long-Term Decision support in the first version; defer it to P2.
- No changes to the existing knowledge-management capture workflow unless needed for shared Hermes routing.

## Assumptions

- WeChat/Hermes is the main user-facing channel.
- Mac activity data can be queried from macOS user idle time. The first version should treat keyboard, mouse, and trackpad input as user activity. Background processes, agents, downloads, builds, or sleep-prevention assertions do not count as user activity.
- SQLite remains the first-version storage layer.
- Life data should be queryable as structured time-series events, so raw life records should not be stored primarily as Markdown.
- Life Assistant records stay in the Life Assistant context in the first version. They are not synced to Obsidian or promoted into Knowledge artifacts.
- Real WeChat notifications should be disabled by default during first implementation. The first usable version should run in dry-run mode until tests and real local idle logs look correct.

## Architecture

The recommended implementation is Scheme A: add the Life Assistant as a separate Hermes agent domain inside `hermes-custom`.

High-level components:

- `agents/life_agent.py`: parses user messages, records life events, handles reminder replies, and generates reviews.
- `db/life_repo.py`: reads and writes Life Assistant data.
- `db/life_schema.py`: owns Life Assistant table definitions and additive migrations.
- `life_scheduler`: evaluates due reminders and review jobs through a minute-level `life_tick`.
- `mac_activity_source`: reads macOS idle time and interruption signals for sedentary detection.
- `bridge/life_bridge.py`: exposes Hermes tool functions for recording, ticking, and reviews.

This keeps the feature close to Hermes routing and WeChat delivery while isolating its data and behavior from the knowledge-management agents.

The first version should expose two Hermes tools:

- `record_life_event(message, received_at?)`: user-facing tool for natural-language life records.
- `life_tick(now?, dry_run?)`: internal cron tool for reminders, reviews, and sedentary state updates.

`life_tick` always returns a standard Agent Result Envelope. It must set `meta.should_notify=true` only when a WeChat message should be sent. The Hermes scheduling layer must inspect this flag and suppress user delivery when it is false.

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

Important fields:

- `id`
- `event_type`
- `occurred_at`
- `ended_at`
- `duration_minutes`
- `source`
- `raw_text`
- `payload_json`
- `status`
- `created_at`
- `updated_at`

`payload_json` carries type-specific fields, such as meal content, exercise type, or reminder id.

Sedentary and sitting metrics use two first-version reporting fields:

- `sitting_total_minutes`: all Mac-active sitting windows, including windows shorter than the sedentary threshold.
- `sedentary_over_limit_minutes`: the full duration of each continuous active window whose duration is greater than or equal to 60 minutes. For an 80-minute window, count 80 minutes, not only the 20 minutes beyond the threshold.

Sitting metrics are split by natural day. Sleep sessions are different: sleep is attributed to the wake date.

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
- `dedupe_key`
- `error`
- `payload_json`
- `created_at`
- `updated_at`

Expected statuses include `scheduled`, `sending`, `sent`, `confirmed`, `snoozed`, `skipped`, `auto_resolved`, `failed`, `expired`, and `missed`.

`dedupe_key` should be unique enough to prevent duplicate sends for the same reminder instance.

### `life_locks`

Stores short-lived cross-process locks for `life_tick`.

Important fields:

- `name`
- `owner`
- `expires_at`
- `created_at`
- `updated_at`

`life_tick` should acquire a lock with about a 50-second TTL before evaluating reminders. If the lock is held and not expired, the tick exits without user notification.

### `life_daily_reviews`

Stores lightweight daily reviews.

Important fields:

- `id`
- `review_date`
- `summary`
- `metrics_json`
- `notes_json`
- `created_at`

### `life_weekly_reviews`

Stores weekly lifestyle reviews.

Important fields:

- `id`
- `week_start`
- `week_end`
- `summary`
- `labels_json`
- `patterns_json`
- `recommendations_json`
- `created_at`

## Reminder Rules

### Sedentary Strong Reminder

Sedentary reminders are stateful and stronger than ordinary reminders.

Defaults:

- Use macOS idle time as the core signal.
- Treat `idle_time <= 5 minutes` as continuous activity.
- Treat `idle_time > 5 minutes` as an activity break.
- Lock, sleep, or logout also count as activity interruption.
- If the Mac activity source fails, suspend Sedentary Alerts and record a warning. Do not infer from stale activity data.
- Trigger after 60 minutes of continuous activity.
- Once triggered, send a WeChat/Hermes reminder every 10 minutes.
- Auto-resolve only when Mac activity is interrupted for more than 5 minutes, or when lock, sleep, or logout is detected.
- User replies do not resolve Sedentary Alerts. The user must act, not reply.
- Allow explicit high-friction management commands such as `今天暂停久坐警报` or `暂停久坐警报 2 小时`. Pausing stops messages only; sitting and sedentary statistics continue to be recorded.

State flow:

1. Mac activity remains continuous for 60 minutes.
2. Hermes enters `sedentary_active`.
3. Hermes sends reminders every 10 minutes.
4. If Mac inactivity exceeds 5 minutes, Hermes records `sedentary_break` and auto-resolves the reminder state.
5. The current continuous sitting timer resets after the break, but the day's cumulative sitting metrics do not reset.

If the user replies `已活动` or an equivalent phrase during an active Sedentary Alert, Hermes should not resolve it. It may explain once per sedentary session that the alert stops only after the user leaves the computer for more than 5 minutes.

The system should record active-window start time, reminder times, resolution time, sitting duration, sedentary-over-limit duration, and resolution source.

### Water Reminder

Defaults:

- Two daytime reminders.
- Send at 10:00 and 16:00 by default, about 6 hours apart.
- Lightweight reminder only.
- No required interaction.
- If the user replies that they drank water, record a `water` event.
- If a `water` event exists within 60 minutes before a planned water reminder, skip that reminder.

### Exercise Reminder

Defaults:

- First reminder at 10:30 every day.
- If the user does not respond and has not recorded exercise, remind every 2 hours.
- Stop for the day when the user records exercise.
- Stop for the day when the user says they will not exercise today.
- If the user sets a specific exercise time, schedule the next reminder for that time. If the user still does not respond after that reminder, return to the 2-hour reminder loop.

The reminder is interactive. Expected statuses include:

- `completed`: the user records exercise.
- `completed_unknown`: the user confirms exercise without type or duration.
- `skipped`: the user says they will not exercise today.
- `scheduled`: the user sets a later exercise time.
- `unresolved`: the user has not responded.

If the user replies `已运动` in an exercise reminder context without type or duration, stop today's exercise reminders, record `completed_unknown`, and ask once for optional details. Do not keep chasing the details.

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
- Outputs a short one-way summary.
- Summarizes the day across sleep, exercise, sedentary time, meals, and water if data exists.
- It does not ask for or collect replies.
- If a sitting window is still active at 00:00, send the review with an "active window still open" note and allow final sitting statistics to be corrected after the break.
- If the review is blocked by higher-priority reminders until 02:00, mark it `missed` instead of sending it later in the night.
- If there is no meal record for the day, mention that the meal record is missing. Do not ask for a reply and do not send standalone meal reminders.

### Weekly Review

Defaults:

- Runs Sunday at 21:30.
- Produces a deeper lifestyle review.
- Covers sleep regularity, exercise consistency, sedentary burden, meal timing/content notes, water reminder effectiveness, and next-week focus.
- It does not ask for or collect replies.
- If the weekly review is blocked by higher-priority reminders until 23:30, mark it `missed`.
- First version should give qualitative labels and recommendations, not numeric scores.
- First version should not create next-week goals automatically.

## Tick Arbitration

`life_tick` runs once per minute from an external cron or Hermes scheduler. It is not a long-lived scheduler in the first version.

Each tick should:

1. Acquire the `life_tick` lock.
2. Read current time, reminder state, and Mac idle time.
3. Update state without sending messages: resolve sedentary state, split cross-midnight sitting windows, expire reminders, and mark missed review windows.
4. Find due reminder candidates.
5. Choose at most one candidate by priority.
6. Create a `life_reminder_logs` row with `status='sending'` and a dedupe key.
7. If dry-run or notifications are disabled, return an envelope with `meta.should_notify=false` and log the selected action.
8. If real notifications are enabled, let the scheduling layer send the selected `chat_preview` only when `meta.should_notify=true`.
9. Mark the log `sent` on success or `failed` on failure.

Reminder priority:

1. Sedentary strong reminder
2. Exercise reminder
3. Sleep reminder
4. Water reminder
5. Daily light review
6. Weekly deep review

Lower-priority reminders remain due if a higher-priority reminder is sent. They should compete again on the next tick instead of being delayed automatically.

Send failure handling:

- Write `sending` before delivery, then update to `sent` or `failed`.
- Sedentary and exercise reminders get short retry behavior after send failure: retry after about 1 minute, up to 2 short retries, then fall back to their normal reminder cadence.
- Water, sleep, and review messages do not need short retries in the first version.

## Conversation Strategy

The first version should use a rule-first parsing strategy with a restricted LLM fallback:

- Use rules for clear supported phrases first.
- If rules cannot parse a message, the LLM fallback may only classify and extract fields for the fixed P1 intent set: sleep, meal, exercise, water, exercise skip, exercise reschedule, sedentary pause, latest-record undo or modify, health consultation, and unsupported.
- The fallback must return structured output with intent, extracted fields, missing required fields, ambiguity notes, and a brief user-facing confirmation, clarification, or rejection.
- Supported intents with complete required fields may create structured events. Unsupported intents, unclear intents, and records missing required fields are not stored as Life Events.
- The fallback must not invent missing life facts. It may only use rule-defined defaults and inferences.
- The assistant only asks a follow-up when the ambiguity affects reminder state or a required field for a supported P1 record is missing.
- The Life Assistant should only handle clearly life-related messages. It must not broadly capture ordinary chat or knowledge-management messages.
- `record_life_event` should reply with a short confirmation for every user-created record in the first version, including key parsed fields so the user can spot parsing errors.
- `record_life_event` is not a question-answering tool. It only handles life records, latest-record undo/modify commands, and active reminder replies.
- First version does not support manual status queries or ad-hoc reviews. Questions such as `我今天坐了多久？` or `今天状态怎么样？` should not be stored as Life Events; reply briefly that the first version will summarize them in the scheduled Daily Light Review or Weekly Deep Review.
- Health consultations such as `我最近睡不好，是不是身体有问题？` must not be stored and must not produce diagnosis. Reply with the safety boundary and recommend qualified help when appropriate.
- The fallback may use the current message and the minimum necessary recent reminder context. It must not read Knowledge context data or create Knowledge artifacts.

Fallback output schema:

```json
{
  "intent": "meal | sleep | exercise | water | exercise_skip | exercise_reschedule | sedentary_pause | undo | modify | health_consultation | unsupported",
  "fields": {},
  "missing_required_fields": [],
  "ambiguity_notes": [],
  "response_type": "confirm | clarify | reject"
}
```

The fallback should keep `fields` minimal and intent-specific. It should not add new top-level keys in the first version.

Follow-up policy:

- Ask at most one follow-up for a single user message.
- Ask once when a supported sleep record is missing start or end time.
- Ask once when a supported meal record is missing meal content.
- Ask once when multiple active reminder contexts could match an ambiguous short reply.
- Do not ask follow-ups for health consultations, manual status queries, ad-hoc reviews, unsupported intents, or ordinary chat.
- If the one follow-up does not produce a clear supported record, stop and do not create a Life Event.

Examples:

- `我睡了` -> `sleep_start`
- `我醒了` -> `sleep_end`, calculate duration from the latest unmatched `sleep_start`
- `昨晚 1 点睡，今天 8 点醒` -> sleep session with explicit start and end
- `午饭吃了牛肉饭` -> `meal`
- `跑步 30 分钟` -> `exercise`
- `喝了` -> `water` if the active context is a water reminder
- `已活动` -> if a Sedentary Alert is active, explain once that the alert only stops after Mac idle time exceeds 5 minutes
- `今天不练了` -> skip today's exercise reminder
- `下午 4 点提醒我运动` -> schedule exercise reminder for that time
- `撤销上一条` -> undo the latest user-created `life_events` row within 30 minutes
- `上一条改成加餐` -> modify the latest user-created `life_events` row within 30 minutes

Field inference boundaries:

- If time is missing, use message receive time unless the active reminder context provides a stricter scheduled time.
- If meal slot is missing, infer it from time and mark the slot as inferred.
- If the user says they exercised but gives no type or duration, record `completed_unknown`.
- If sleep start or sleep end is missing, ask for the missing time instead of guessing.
- If meal content is missing, do not create a meal record. Ask for the content or reject the record.
- Do not infer facts such as exercise duration, meal content, or sleep start time from general phrasing.

Short replies use active reminder context:

- Keyword-clear replies can bind directly, such as `喝了` to water.
- Ambiguous replies bind to the recent active reminder only when there is one clear candidate.
- If multiple reminders are active and the reply is ambiguous, ask a short clarification.

Sleep conflict handling:

- If `我睡了` arrives while an unmatched sleep start exists, ask whether to close the previous sleep record or start over.
- If `我醒了` arrives without an unmatched sleep start, ask for the approximate sleep start time.
- Natural-language explicit times override message receive time. Otherwise, use the message receive time.
- Sleep sessions are attributed to the wake date.

Meal handling:

- If the meal slot is explicit, use it.
- If the meal slot is not explicit, infer from time and mark the slot as inferred.
- Do not assume the user must eat three meals per day.
- Do not provide instant health commentary for meals. Use daily or weekly review instead.

## Review Behavior

Daily review should be concise and low-friction. It should not try to produce a full report every night, and it should not require a reply.

Weekly review should be more analytical. It should identify patterns and propose small adjustments, while staying away from medical diagnosis.

Useful weekly review sections:

- Rhythm summary
- Sleep pattern
- Exercise consistency
- Sedentary risk
- Meal observations
- Water reminders
- Suggested focus for next week

Weekly review should not create goals in the first version. It may suggest one or more next steps, but those suggestions do not change reminder rules unless the user later gives an explicit command.

## Configuration And Rollout

First-version configuration should start from code defaults. Only limited same-day controls are supported from WeChat:

- `今天暂停久坐警报`
- `暂停久坐警报 2 小时`
- `今天不练了`
- exercise reminder rescheduling, such as `下午 4 点提醒我运动`

Permanent reminder configuration changes through WeChat are out of scope for the first version.

Real notifications must be off by default. Initial rollout steps:

1. Run all unit and state-machine tests.
2. Run `life_tick` in dry-run mode.
3. Observe real local macOS idle logs for 15-30 minutes.
4. Confirm state transitions and due reminder choices look correct.
5. Enable real WeChat notifications explicitly.

## Observability

Add a dedicated `data/life.log` for Life Assistant tick choices. Each tick should log key facts in a compact structured form:

- tick time
- idle seconds or activity-source failure
- current sedentary state
- due candidates
- selected reminder
- `should_notify`
- send result
- warnings or errors

The SQLite event and reminder tables remain the durable source of state. The log is for debugging and operations.

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
- The user can record meals, exercise, and water from WeChat.
- Sedentary strong reminders repeat every 10 minutes after 60 minutes of continuous Mac activity and stop after more than 5 minutes of inactivity.
- Sedentary Alerts cannot be resolved by replying. They stop only after macOS idle time exceeds 5 minutes, lock, sleep, logout, or explicit alert pause.
- Daily statistics include exactly two sitting metrics: `sitting_total_minutes` and `sedentary_over_limit_minutes`.
- Exercise reminders begin at 10:30, repeat every 2 hours when unresolved, and stop after exercise is recorded or skipped for the day.
- Water reminders occur twice during the day and do not require interaction.
- Water reminders skip if a water event was recorded within 60 minutes before the planned reminder.
- Sleep reminders are lightweight and non-interactive.
- Daily reviews run at 00:00.
- Weekly reviews run Sunday at 21:30.
- Life Assistant data is stored in `life_*` tables and does not pollute existing knowledge-management tables.
- `life_tick` sends at most one reminder per tick and suppresses user delivery unless `meta.should_notify=true`.
- `life_tick` uses a SQLite lock and reminder dedupe keys to avoid duplicate sends.
- First rollout starts in dry-run mode and requires explicit enablement before real WeChat notifications.
- `data/life.log` records tick choices and failures.

## Test Focus

The first implementation should emphasize state-machine tests, not only repository and parser tests:

- SQLite schema and additive migration for `life_*` tables only.
- Sleep start/end calculation and conflict prompts.
- Meal slot inference without assuming three meals per day.
- Exercise reminder states: unresolved, scheduled, completed, completed_unknown, skipped.
- Water reminder skip when recent water is recorded.
- Sedentary trigger at `>= 60 minutes`, 10-minute repeat, action-only resolution after idle time exceeds 5 minutes, pause behavior, and activity-source failure.
- Cross-midnight sitting split by natural day.
- Sleep attribution to wake date.
- `life_tick` priority arbitration with at most one selected reminder.
- `meta.should_notify=false` suppression for no-op and dry-run ticks.
- SQLite tick lock and reminder dedupe behavior.
- Send failure state transitions and short retry behavior for sedentary and exercise reminders.
- 30-minute undo/modify window for the latest user-created life event.

## Remaining Implementation Choices

- `mac_activity_source` should probably read macOS HID idle time, but the exact command/API wrapper should be selected and tested during implementation.
- Daily and weekly reviews should be stored in the Life Assistant data store. Obsidian export is not part of the first version.
- Exact wording for each reminder message can be tuned during implementation, but wording must match each reminder's interaction model.
