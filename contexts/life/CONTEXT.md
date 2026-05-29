# Hermes Life Assistant Context

Hermes Life Assistant is a personal life-rhythm and reflection context. It uses Hermes/WeChat to record daily behavior, run reminders, and support lightweight reflection without becoming a medical diagnosis system or a knowledge-management capture workflow.

## Language

**Life Assistant**:
The Hermes domain that tracks personal rhythm, lifestyle records, reminders, reviews, and lightweight long-term decisions.
_Avoid_: health bot, doctor, knowledge capture agent.

**Life Event**:
A time-stamped record of something the user did, reported, or decided in the Life Assistant context.
_Avoid_: capture, note, chat message, reminder, review.

**Life Note**:
An unstructured **Life Event** used when a user message is life-related but not confidently parsed into a more specific event type.
_Avoid_: Capture Note, Raw Evidence, Garden Card.

**Sitting Window**:
A Mac-activity-inferred sitting period where idle time indicates the user is actively using the computer, ending when idle time exceeds the break threshold or the Mac locks, sleeps, or logs out.
_Avoid_: session, work session, Mac usage as the canonical term.

**Activity Break**:
The end of a **Sitting Window**, proven by Mac idle time exceeding the break threshold or by lock, sleep, or logout.
Chinese label: `离座休息`.
_Avoid_: reply confirmation, claimed break.

**Sedentary Alert**:
A strong reminder created when a **Sitting Window** reaches the sedentary threshold; it repeats until an **Activity Break** occurs or a clear pause command is given.
_Avoid_: reminder that can be dismissed by saying "done".

**Sitting Total**:
The daily total of all **Sitting Windows**, including windows shorter than the sedentary threshold.
Chinese label: `累计坐着时长`.
_Avoid_: Mac usage total.

**Sedentary Over-Limit Total**:
The daily total of full **Sitting Windows** whose duration is greater than or equal to the sedentary threshold.
Chinese label: `久坐超标时长`.
_Avoid_: minutes beyond the threshold, excess minutes.

**Water Reminder**:
A lightweight reminder to drink water; it does not require a reply and may be skipped when a recent water event already exists.
_Avoid_: hydration goal, water challenge.

**Exercise Reminder**:
An interactive reminder to exercise that remains unresolved until the user records exercise, schedules a later exercise time, or explicitly skips exercise for the day.
_Avoid_: workout plan, fitness program.

**Sleep Reminder**:
A lightweight reminder to prepare for sleep; it does not require interaction and does not repeat in a loop.
_Avoid_: sleep alarm, bedtime enforcement.

**Sleep Session**:
A sleep interval recorded from a sleep start and sleep end, attributed to the wake date.
_Avoid_: splitting sleep by natural day.

**Meal Record**:
A **Life Event** describing when and what the user ate, with an explicit or inferred meal slot.
_Avoid_: nutrition analysis, calorie record.

**Daily Light Review**:
A low-friction daily review that summarizes the day and may ask one or two optional questions.
_Avoid_: full report, required check-in.

**Weekly Deep Review**:
A qualitative weekly lifestyle review that identifies patterns and small suggestions without numeric scores or automatic goal creation.
_Avoid_: medical assessment, performance score.

**Reflection**:
A **Life Event** that captures a thought, lesson, or self-review item inside the Life Assistant context only.
_Avoid_: Garden Card, Accepted Garden Card.

**Long-Term Decision**:
A formal Life Assistant record of a decision that includes review time, rationale, and expected outcome.
_Avoid_: casual note, goal.

**Pending Decision**:
A provisional record created when the user starts a long-term decision but has not yet provided review time, rationale, and expected outcome.
_Avoid_: completed decision.

## Flagged Ambiguities

- "Activity" can mean exercise or Mac activity. Resolved: use **Exercise** for deliberate physical activity, and **Sitting Window** or **Activity Break** for Mac-idle-derived behavior.
- A user saying "已活动" is not an **Activity Break** (`离座休息`). Resolved: an **Activity Break** must be proven by Mac idle time, lock, sleep, or logout.
- A **Sitting Window** is inferred from Mac activity; it is not a direct body-position measurement.
- "久坐时间" can mean all sitting or only risky sitting. Resolved: use **Sitting Total** for all sitting windows and **Sedentary Over-Limit Total** for sitting windows at or above the threshold.
- `久坐超标时长` counts the full over-limit **Sitting Window**, not only the minutes beyond the sedentary threshold.
- "Review" exists in the Knowledge context as recall practice. Resolved: Life Assistant uses **Daily Light Review** and **Weekly Deep Review** for lifestyle reflection; Knowledge uses Review Items.
- **Daily Light Review** and **Weekly Deep Review** are derived review records, not **Life Events**.
- **Water Reminder**, **Exercise Reminder**, **Sleep Reminder**, and **Sedentary Alert** are reminder records, not **Life Events**.
- **Reflection** is not a **Garden Card**. It remains inside Life Assistant and does not promote into the Knowledge context in the first version.
- **Life Assistant** and Knowledge may share one code repository, but they should not share domain ownership. Life Assistant owns life rhythm and lifestyle records; Knowledge owns captures, notes, themes, and viewpoints.
- **Long-Term Decision** is not a goal system in the first version. It is a decision record with a review time, rationale, and expected outcome.

## Example Dialogue

Dev: "If the user replies '已活动' to a sedentary alert, do we stop reminding?"

Domain expert: "No. A Sedentary Alert stops only after an Activity Break is detected. The user has to leave the computer long enough for idle time to prove it."

Dev: "A user used the Mac for 50 minutes and then left. Is that sedentary?"

Domain expert: "It contributes to Sitting Total, but not Sedentary Over-Limit Total."

Dev: "A user used the Mac for 80 minutes. How much goes into Sedentary Over-Limit Total?"

Domain expert: "All 80 minutes. We do not count only the 20 minutes beyond the threshold."

Dev: "Should a meal record judge whether the meal was healthy immediately?"

Domain expert: "No. Meal Records are low-friction records. Commentary belongs in the Daily Light Review or Weekly Deep Review."

Dev: "Do reminders belong in the Life Event stream?"

Domain expert: "No. Life Events record life facts. Reminder history belongs to reminder records."

Dev: "Can a reflection become a Garden Card automatically?"

Domain expert: "No. Reflection belongs to Life Assistant; Garden Card belongs to Knowledge. The first version keeps the systems separate."

Dev: "Can a long-term decision be saved without a review date?"

Domain expert: "No. It stays a Pending Decision until review time, rationale, and expected outcome are provided."
