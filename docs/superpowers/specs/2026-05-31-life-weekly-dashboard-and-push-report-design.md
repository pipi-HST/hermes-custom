# Life Weekly Dashboard And Push Report Design

## Goal

Add a weekly Life view for data inspection, and upgrade the weekly review from a simple metric summary into a concise health-expert style report that is pushed proactively. The dashboard remains a data surface; the report is a short weekly message, not a long article.

## Confirmed Product Decisions

- The Life dashboard will use separate `day` and `week` modes.
- The weekly dashboard will not become an article/report reader.
- The weekly dashboard will show weekly data, trends, raw records, and delivery status.
- The weekly analysis report will be generated weekly and pushed proactively.
- WeChat and Feishu receive the same concise report in P1.
- The weekly report must stay within 600 Chinese characters.
- The database stores the concise report, metrics, evidence, and delivery metadata so the system can dedupe, audit, and debug it.
- The tone is health-expert style: professional, direct, restrained, evidence-based, and not a medical diagnosis.

## Weekly Dashboard Scope

The weekly dashboard answers: “What happened this week?”

It should include:

- Week selector: current week, previous week, next week.
- Top metrics:
  - sleep record days, total sleep, average sleep
  - exercise days, total exercise minutes
  - sitting total minutes, sedentary over-limit minutes
  - meal record count, meal coverage by slot
  - water count
- Seven-day trend table:
  - each day as one column
  - rows for sleep, exercise, sitting, sedentary over-limit, meals, water
  - compact values, not charts that require heavy visual interpretation
- Weekly raw record list:
  - sleep sessions
  - meal records
  - exercise records
  - sedentary windows
  - water records
- Weekly review delivery status:
  - not generated
  - generated
  - sent to WeChat
  - sent to Feishu
  - failed

The weekly dashboard should not show a report article. It may show a short status and a one-line headline if already generated.

## Weekly Push Report Scope

The weekly push report answers: “What are the most important health and routine signals this week?”

The report must fit within 600 Chinese characters. It should not try to cover every category equally when the data does not support that. It should prioritize the most important signals.

The report structure:

1. Overall assessment
2. Key findings, 3-5 bullets
3. Next week suggestions, 2-3 bullets

The report must distinguish between:

- “The behavior looks unhealthy.”
- “The record is missing, so the system cannot judge.”
- “The pattern is visible but evidence is thin.”

The report must avoid medical diagnosis. It can discuss lifestyle risk and routine quality.

## Delivery Strategy

### WeChat And Feishu

WeChat and Feishu receive the same concise weekly report in P1. Target length: no more than 600 Chinese characters.

The report includes:

- overall assessment
- 3-5 key findings based on actual records
- 2-3 suggestions for next week

Do not split the report into multiple WeChat messages in P1. Do not create a separate long Feishu version in P1.

### Database

The database stores the report text and delivery metadata. The stored record should be the canonical weekly review result.

At minimum, each weekly report stores:

- week start and week end
- headline
- report text
- structured metrics
- evidence items used by the report
- recommendations
- delivery status for WeChat and Feishu
- generation source and created timestamp

## Generation Architecture

Use a hybrid system:

1. Deterministic fact pack builder
   - Reads Life events and reminders for the selected week.
   - Computes metrics and trend rows.
   - Selects representative evidence.
   - Marks missing data explicitly.

2. LLM report writer
   - Receives only the fact pack.
   - Writes one concise health-expert style report.
   - Keeps the report within 600 Chinese characters.
   - Must not invent facts outside the fact pack.

3. Deterministic persistence and delivery
   - Stores the concise report and supporting structured data.
   - Sends the same concise report to WeChat.
   - Sends the same concise report to Feishu if configured.
   - Records delivery status separately from generation status.

If the LLM call fails, the system should fall back to a deterministic weekly summary rather than skipping the weekly review entirely.

## Data Model Direction

The current `life_weekly_reviews` table can already store summary, labels, patterns, and recommendations. It needs only a small extension for report evidence and delivery metadata.

Add either:

- new columns to `life_weekly_reviews`, or
- a new companion table such as `life_weekly_report_deliveries`.

Recommended first version:

- Extend `life_weekly_reviews` with structured JSON fields for evidence and delivery status.
- Avoid introducing a second table until there are multiple delivery attempts per channel.

## API Direction

Add a weekly endpoint or extend the existing Life API to support week mode.

Recommended first version:

- `GET /api/life/week?date=YYYY-MM-DD`

Response includes:

- week start and week end
- weekly metrics
- seven daily trend rows
- record groups
- weekly review status and short headline

The existing `/api/life/reviews` can keep returning the current review object for the selected date. The new `/api/life/week` gives the dashboard a stable data-first shape.

## Dashboard Direction

Update `/life` with a segmented control:

- Day
- Week

Day mode remains close to the current dashboard.

Week mode contains:

- week navigation
- top metric tiles
- seven-day trend table
- grouped weekly records
- weekly report delivery status and the concise report headline

Do not put a report article into week mode in P1. A concise report preview is acceptable only if it does not crowd out the weekly data.

## Testing Strategy

Backend tests:

- fact pack computes weekly metrics from mixed Life events
- weekly review stores concise report and evidence
- weekly report is length bounded to 600 Chinese characters
- LLM failure falls back to deterministic weekly summary
- weekly API returns stable empty shape when no data exists

Frontend tests or TypeScript checks:

- week API types match backend shape
- dashboard can render empty week
- dashboard can render a populated week
- day mode remains usable

Runtime checks:

- `python3 scripts/life_smoke.py`
- `python3 scripts/life_audit.py`
- Life pytest subset
- dashboard TypeScript check

## Out Of Scope For This Iteration

- Manual dashboard editing.
- Monthly reports.
- Medical diagnosis.
- Nutrition macros or calorie estimation.
- Apple Health or wearable integration.
- Multi-message WeChat report pagination.
- Separate long Feishu report.
- Letting the user query Life manually from the dashboard.

## Open Implementation Decision

Feishu delivery details depend on the existing Hermes delivery bridge and available destinations. Implementation should inspect current delivery utilities before deciding whether Feishu receives the P1 concise report immediately or whether the report is stored with Feishu delivery status pending.
