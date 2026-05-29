# Hermes Life Assistant Context

Hermes Life Assistant is a personal life-rhythm context. It uses Hermes/WeChat to record daily behavior and run reminders without becoming a medical diagnosis system or a knowledge-management capture workflow.

## Language

**Life Assistant**:
The Hermes domain that tracks personal rhythm, lifestyle records, reminders, and reviews.
_Avoid_: health bot, doctor, knowledge capture agent.

**Life Event**:
A time-stamped record of something the user did, reported, or decided in the Life Assistant context.
_Avoid_: capture, note, chat message, reminder, review.

**Sitting Window**:
A Mac-activity-inferred sitting period where idle time indicates the user is actively using the computer, ending when idle time exceeds the break threshold or the Mac locks, sleeps, or logs out.
_Avoid_: session, work session, Mac usage as the canonical term.

**Activity Break**:
The end of a **Sitting Window**, proven by Mac idle time exceeding the break threshold or by lock, sleep, or logout.
Chinese label: `离座休息`.
_Avoid_: reply confirmation, claimed break.

**Sedentary Alert**:
A strong reminder created when a **Sitting Window** reaches the sedentary threshold; it repeats until an **Activity Break** occurs or a clear pause command is given.
Chinese label: `久坐警报`.
_Avoid_: reminder that can be dismissed by saying "done"; user-facing system text should not call it `久坐提醒`.

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
A low-friction one-way daily review that summarizes the day.
_Avoid_: full report, required check-in, reply prompt.

**Weekly Deep Review**:
A one-way qualitative weekly lifestyle review that identifies patterns and small suggestions without numeric scores or automatic goal creation.
_Avoid_: medical assessment, performance score, reply prompt.

## Flagged Ambiguities

- "Activity" can mean exercise or Mac activity. Resolved: use **Exercise** for deliberate physical activity, and **Sitting Window** or **Activity Break** for Mac-idle-derived behavior.
- A user saying "已活动" is not an **Activity Break** (`离座休息`). Resolved: an **Activity Break** must be proven by Mac idle time, lock, sleep, or logout.
- A **Sitting Window** is inferred from Mac activity; it is not a direct body-position measurement.
- `久坐提醒` may be accepted as a user-input alias, but system-facing language should use **Sedentary Alert** (`久坐警报`).
- "久坐时间" can mean all sitting or only risky sitting. Resolved: use **Sitting Total** for all sitting windows and **Sedentary Over-Limit Total** for sitting windows at or above the threshold.
- `久坐超标时长` counts the full over-limit **Sitting Window**, not only the minutes beyond the sedentary threshold.
- "Review" exists in the Knowledge context as recall practice. Resolved: Life Assistant uses **Daily Light Review** and **Weekly Deep Review** for lifestyle rhythm review; Knowledge uses Review Items.
- **Daily Light Review** and **Weekly Deep Review** are derived review records, not **Life Events**.
- **Daily Light Review** and **Weekly Deep Review** are one-way outputs. They do not create reply contexts and do not collect replies.
- **Water Reminder**, **Exercise Reminder**, **Sleep Reminder**, and **Sedentary Alert** are reminder records, not **Life Events**.
- Life Assistant P1 does not support unstructured notes. Unsupported records and low-confidence ambiguous records should not become **Life Events**.
- **Life Assistant** and Knowledge may share one code repository, but they should not share domain ownership. Life Assistant owns life rhythm and lifestyle records; Knowledge owns captures, notes, themes, and viewpoints.
- Reflection and Long-Term Decision are P2 concepts, not first-version Life Assistant concepts.

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

Dev: "Should the first version record long-term decisions?"

Domain expert: "No. Long-Term Decision is a P2 concept. First version focuses on the life-rhythm pipeline."
