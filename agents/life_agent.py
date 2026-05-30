"""Life Assistant agent core logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from agents.life_intent_parser import LifeIntent, LifeIntentParser
from db.life_repo import LifeRepo


@dataclass(frozen=True)
class ActivitySnapshot:
    idle_seconds: int | None
    available: bool = True
    locked: bool = False
    sleeping: bool = False
    logged_out: bool = False


@dataclass(frozen=True)
class LifeConfig:
    sedentary_threshold_minutes: int = 60
    sedentary_repeat_minutes: int = 10
    break_threshold_minutes: int = 5
    reminder_due_window_minutes: int = 15
    exercise_first_time: str = "10:30"
    exercise_repeat_hours: int = 2
    sleep_reminder_time: str = "23:30"
    water_times: tuple[str, str] = ("10:00", "16:00")
    notifications_enabled: bool = False


class LifeAgent:
    def __init__(
        self,
        repo: LifeRepo,
        config: LifeConfig | None = None,
        parser: LifeIntentParser | None = None,
    ):
        self.repo = repo
        self.config = config or LifeConfig()
        self.parser = parser or LifeIntentParser()

    def record_life_event(
        self,
        message: str,
        *,
        received_at: str | None = None,
    ) -> dict[str, Any]:
        received = _parse_datetime(received_at) if received_at else _now()
        text = message.strip()
        intent = self.parser.parse(text, received_at=received)

        if intent.intent == "manual_status_query":
            return _rejected("第一版不支持手动查询，我会在定时复盘里总结这些数据。")

        if intent.intent == "health_consultation":
            return _rejected("我不能做医学诊断；如果症状持续、明显影响生活，建议咨询医生或专业人士。")

        if intent.intent == "sedentary_activity_reply":
            return _rejected("久坐警报不能靠回复解除；离开电脑超过5分钟后我会自动停止提醒。")

        if intent.intent == "pause_sedentary":
            try:
                pause_until = _parse_datetime(intent.scheduled_for)
            except ValueError:
                return _rejected("这条我暂时不能作为生活记录保存。")
            self.repo.create_reminder_log(
                reminder_type="sedentary",
                scheduled_for=pause_until.isoformat(),
                status="paused",
                dedupe_key=f"sedentary_pause:{received.isoformat()}",
                payload={"duration_minutes": intent.duration_minutes},
            )
            return {
                **_reminder_updated(f"已暂停久坐警报到{_fmt_time(pause_until)}；坐姿统计仍会继续记录。"),
                "scheduled_for": pause_until.isoformat(),
            }

        if intent.intent == "skip_exercise":
            self.repo.create_reminder_log(
                reminder_type="exercise",
                scheduled_for=received.isoformat(),
                status="skipped",
                dedupe_key=f"exercise_skip:{received.date().isoformat()}",
                payload={"reason": "user_skip"},
            )
            return _reminder_updated("今天不练已记录，今天不会继续追运动提醒。")

        if intent.intent == "reschedule_exercise":
            try:
                scheduled_for = _parse_datetime(intent.scheduled_for)
            except ValueError:
                return _rejected("这条我暂时不能作为生活记录保存。")
            payload = {"source": "user_reschedule", **intent.payload}
            self.repo.create_reminder_log(
                reminder_type="exercise",
                scheduled_for=scheduled_for.isoformat(),
                status="scheduled",
                dedupe_key=f"exercise_reschedule:{scheduled_for.isoformat()}",
                payload=payload,
            )
            exercise_type = payload.get("exercise_type", "运动")
            return {
                **_reminder_updated(f"已把{exercise_type}提醒改到{_fmt_time(scheduled_for)}。"),
                "scheduled_for": scheduled_for.isoformat(),
            }

        if intent.intent == "undo_latest":
            latest = self.repo.latest_user_event(
                now=received.isoformat(),
                window_minutes=30,
            )
            if not latest:
                return _rejected("没有找到 30 分钟内可撤销的生活记录。")
            updated = self.repo.update_event(latest["id"], status="undone")
            return {"status": "undone", "event": updated, "chat_preview": "已撤销上一条生活记录。"}

        if intent.intent == "modify_latest":
            return self._modify_latest_event(intent, received)

        if intent.intent == "record_event":
            return self._record_structured_event(intent, message, received)

        return _rejected("这条我暂时不能作为生活记录保存。")

    def _record_structured_event(
        self,
        intent: LifeIntent,
        message: str,
        received: datetime,
    ) -> dict[str, Any]:
        try:
            occurred = _parse_datetime(intent.occurred_at) if intent.occurred_at else received
        except ValueError:
            return _rejected("这条我暂时不能作为生活记录保存。")

        if intent.event_type == "sleep_start":
            event = self.repo.create_event(
                event_type="sleep_start",
                occurred_at=occurred.isoformat(),
                source="wechat",
                raw_text=message,
                payload=intent.payload or None,
            )
            paired_end = self._backfill_next_sleep_end(event)
            if paired_end and paired_end.get("duration_minutes") is not None:
                return _recorded(
                    event,
                    f"已记录睡觉时间：{_fmt_time(occurred)}，睡了{_fmt_duration(paired_end['duration_minutes'])}。",
                )
            return _recorded(event, f"已记录睡觉时间：{_fmt_time(occurred)}。")

        if intent.event_type == "sleep_end":
            start = self._latest_unmatched_sleep_start()
            duration = None
            if start:
                started_at = _parse_datetime(start["occurred_at"])
                duration = _duration_minutes_between(started_at, occurred)
            payload = {"wake_date": occurred.date().isoformat(), **intent.payload}
            event = self.repo.create_event(
                event_type="sleep_end",
                occurred_at=occurred.isoformat(),
                duration_minutes=duration,
                source="wechat",
                raw_text=message,
                payload=payload,
            )
            if duration is None:
                return _recorded(event, "已记录醒来时间；还缺睡觉时间，可以补一句大概几点睡。")
            return _recorded(event, f"已记录醒来时间：{_fmt_time(occurred)}，睡了{_fmt_duration(duration)}。")

        if intent.event_type == "sleep_session":
            try:
                ended_at = _parse_datetime(str(intent.payload.get("ended_at", "")))
            except ValueError:
                return _rejected("这条我暂时不能作为生活记录保存。")
            duration = _duration_minutes_between(occurred, ended_at)
            self.repo.create_event(
                event_type="sleep_start",
                occurred_at=occurred.isoformat(),
                source="wechat",
                raw_text=message,
            )
            event = self.repo.create_event(
                event_type="sleep_end",
                occurred_at=ended_at.isoformat(),
                duration_minutes=duration,
                source="wechat",
                raw_text=message,
                payload={"wake_date": ended_at.date().isoformat()},
            )
            return _recorded(event, f"已记录睡眠：{_fmt_time(occurred)} 到 {_fmt_time(ended_at)}，睡了{_fmt_duration(duration)}。")

        if intent.event_type == "exercise":
            if intent.status != "completed_unknown":
                if intent.duration_minutes is None or "exercise_type" not in intent.payload:
                    return _rejected("这条我暂时不能作为生活记录保存。")
            event = self.repo.create_event(
                event_type="exercise",
                occurred_at=occurred.isoformat(),
                duration_minutes=intent.duration_minutes,
                source="wechat",
                raw_text=message,
                status=intent.status or "recorded",
                payload=intent.payload or None,
            )
            if intent.status == "completed_unknown":
                return _recorded(event, "已记录今天运动完成；可以之后补充类型和时长。")
            return _recorded(
                event,
                f"已记录运动：{intent.payload['exercise_type']} {intent.duration_minutes} 分钟。",
            )

        if intent.event_type == "meal":
            if "meal_slot" not in intent.payload or "content" not in intent.payload:
                return _rejected("这条我暂时不能作为生活记录保存。")
            event = self.repo.create_event(
                event_type="meal",
                occurred_at=occurred.isoformat(),
                source="wechat",
                raw_text=message,
                payload=intent.payload,
            )
            return _recorded(
                event,
                f"已记录{_meal_label(intent.payload['meal_slot'])}：{intent.payload['content']}。",
            )

        if intent.event_type == "water":
            event = self.repo.create_event(
                event_type="water",
                occurred_at=occurred.isoformat(),
                source="wechat",
                raw_text=message,
            )
            return _recorded(event, f"已记录喝水：{_fmt_time(occurred)}。")

        return _rejected("这条我暂时不能作为生活记录保存。")

    def _modify_latest_event(self, intent: LifeIntent, received: datetime) -> dict[str, Any]:
        latest = self.repo.latest_user_event(
            now=received.isoformat(),
            window_minutes=30,
        )
        if not latest:
            return _rejected("没有找到 30 分钟内可修改的生活记录。")

        change_text = str(intent.payload.get("change_text") or "").strip()
        if not change_text:
            return _rejected("这条修改指令不完整，暂时不能保存。")

        updated = self._updated_latest_meal(latest, change_text, received)
        if not updated:
            return _rejected("这条修改我暂时只支持改最近一条饮食记录。")

        payload = updated["payload_json"]
        return {
            "status": "modified",
            "event": updated,
            "chat_preview": f"已把上一条改为{_meal_label(payload['meal_slot'])}：{payload['content']}。",
        }

    def _updated_latest_meal(
        self,
        latest: dict[str, Any],
        change_text: str,
        received: datetime,
    ) -> dict[str, Any] | None:
        if latest["event_type"] != "meal":
            return None

        slot = _meal_slot_from_text(change_text)
        if slot and "吃" not in change_text:
            payload = dict(latest.get("payload_json") or {})
            if not payload.get("content"):
                return None
            payload["meal_slot"] = slot
            payload["meal_slot_inferred"] = False
            return self.repo.update_event(latest["id"], payload=payload)

        parsed = self.parser.parse(change_text, received_at=received)
        if parsed.intent != "record_event" or parsed.event_type != "meal":
            return None
        if "meal_slot" not in parsed.payload or "content" not in parsed.payload:
            return None
        return self.repo.update_event(latest["id"], payload=parsed.payload)

    def life_tick(
        self,
        *,
        now: str | None = None,
        activity: ActivitySnapshot | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        current = _parse_datetime(now) if now else _now()
        activity = activity or ActivitySnapshot(idle_seconds=None, available=False)
        warnings: list[str] = []
        candidates: list[dict[str, Any]] = []

        if activity.available:
            sedentary = self._sedentary_candidate(current, activity)
            if sedentary:
                candidates.append(sedentary)
        else:
            warnings.append("mac_activity_unavailable")

        exercise = self._exercise_candidate(current)
        if exercise:
            candidates.append(exercise)
        sleep = self._sleep_candidate(current)
        if sleep:
            candidates.append(sleep)
        water = self._water_candidate(current)
        if water:
            candidates.append(water)
        daily_review = self._daily_review_candidate(current)
        if daily_review:
            candidates.append(daily_review)

        candidates.sort(key=lambda item: item["priority"])
        selected = candidates[0] if candidates else None
        should_notify = bool(selected and self.config.notifications_enabled and not dry_run)
        if selected:
            self.repo.create_reminder_log(
                reminder_type=selected["reminder_type"],
                scheduled_for=current.isoformat(),
                status="sending" if should_notify else "scheduled",
                dedupe_key=selected["dedupe_key"],
                payload={"dry_run": dry_run},
            )
        return {
            "status": "selected" if selected else "idle",
            "selected": selected,
            "chat_preview": selected["message"] if selected else "Life Assistant 本次 tick 无需提醒。",
            "warnings": warnings,
            "meta": {"should_notify": should_notify, "dry_run": dry_run},
        }

    def _latest_unmatched_sleep_start(self) -> dict[str, Any] | None:
        row = self.repo.conn.execute(
            """SELECT * FROM life_events
               WHERE event_type = 'sleep_start'
               ORDER BY occurred_at DESC, rowid DESC
               LIMIT 1"""
        ).fetchone()
        if not row:
            return None
        end = self.repo.conn.execute(
            """SELECT * FROM life_events
               WHERE event_type = 'sleep_end' AND occurred_at > ?
               ORDER BY occurred_at ASC LIMIT 1""",
            (row["occurred_at"],),
        ).fetchone()
        return None if end else self.repo._event_row_to_dict(row)

    def _backfill_next_sleep_end(self, sleep_start: dict[str, Any]) -> dict[str, Any] | None:
        row = self.repo.conn.execute(
            """SELECT * FROM life_events
               WHERE event_type = 'sleep_end'
                 AND duration_minutes IS NULL
                 AND occurred_at > ?
               ORDER BY occurred_at ASC, rowid ASC
               LIMIT 1""",
            (sleep_start["occurred_at"],),
        ).fetchone()
        if not row:
            return None
        sleep_end = self.repo._event_row_to_dict(row)
        started_at = _parse_datetime(sleep_start["occurred_at"])
        ended_at = _parse_datetime(sleep_end["occurred_at"])
        if started_at.date() != ended_at.date():
            return None
        duration = _duration_minutes_between(started_at, ended_at)
        return self.repo.update_event(sleep_end["id"], duration_minutes=duration)

    def _sedentary_candidate(
        self,
        current: datetime,
        activity: ActivitySnapshot,
    ) -> dict[str, Any] | None:
        active_window = self._active_sitting_window()
        if activity.locked or activity.sleeping or activity.logged_out:
            if active_window:
                self._close_sitting_window(active_window, current, "mac_interrupted")
            return None
        if activity.idle_seconds is None:
            return None
        if activity.idle_seconds > self.config.break_threshold_minutes * 60:
            if active_window:
                self._close_sitting_window(active_window, current, "idle_break")
            return None

        if not active_window:
            self.repo.create_event(
                event_type="sedentary_start",
                occurred_at=current.isoformat(),
                source="mac_activity",
                status="active",
                payload={"idle_seconds": activity.idle_seconds},
            )
            return None

        started_at = _parse_datetime(active_window["occurred_at"])
        sitting_minutes = _duration_minutes_between(started_at, current)
        if sitting_minutes < self.config.sedentary_threshold_minutes:
            return None
        if self._is_sedentary_paused(current):
            return None

        return {
            "priority": 1,
            "reminder_type": "sedentary",
            "dedupe_key": f"sedentary:{current.strftime('%Y-%m-%dT%H:%M')}",
            "message": "久坐警报：起来活动一下，离开电脑超过5分钟后我会停止提醒。",
        }

    def _active_sitting_window(self) -> dict[str, Any] | None:
        row = self.repo.conn.execute(
            """SELECT * FROM life_events
               WHERE event_type = 'sedentary_start' AND status = 'active'
               ORDER BY occurred_at DESC, rowid DESC
               LIMIT 1"""
        ).fetchone()
        return self.repo._event_row_to_dict(row) if row else None

    def _is_sedentary_paused(self, current: datetime) -> bool:
        rows = self.repo.conn.execute(
            """SELECT scheduled_for FROM life_reminder_logs
               WHERE reminder_type = 'sedentary' AND status = 'paused'
               ORDER BY scheduled_for DESC"""
        ).fetchall()
        return any(_parse_datetime(row["scheduled_for"]) > current for row in rows)

    def _close_sitting_window(
        self,
        active_window: dict[str, Any],
        current: datetime,
        resolution_source: str,
    ) -> None:
        started_at = _parse_datetime(active_window["occurred_at"])
        if _seconds_between(started_at, current) < 0:
            return
        duration_minutes = _duration_minutes_between(started_at, current)
        self.repo.update_event(
            active_window["id"],
            ended_at=current.isoformat(),
            duration_minutes=duration_minutes,
            status="auto_resolved",
        )
        self.repo.create_event(
            event_type="sedentary_break",
            occurred_at=current.isoformat(),
            duration_minutes=duration_minutes,
            source="mac_activity",
            payload={
                "sitting_window_id": active_window["id"],
                "resolution_source": resolution_source,
            },
        )

    def _exercise_candidate(self, current: datetime) -> dict[str, Any] | None:
        if current.strftime("%H:%M") < self.config.exercise_first_time:
            return None
        if self._has_event_on_day("exercise", current):
            return None
        if self._has_reminder_log_on_day("exercise", "skipped", current):
            return None
        scheduled_log = self._latest_exercise_reminder_log(current)
        if scheduled_log:
            scheduled = _parse_datetime(scheduled_log["scheduled_for"])
            if current < scheduled:
                return None
            payload = scheduled_log.get("payload_json") or {}
            if payload.get("source") != "user_reschedule":
                next_repeat = scheduled + timedelta(hours=self.config.exercise_repeat_hours)
                if current < next_repeat:
                    return None
        return {
            "priority": 2,
            "reminder_type": "exercise",
            "dedupe_key": f"exercise:{current.strftime('%Y-%m-%dT%H:%M')}",
            "message": "运动提醒：今天找个时间动一动吧。回复已运动、今天不练了，或告诉我具体时间。",
        }

    def _sleep_candidate(self, current: datetime) -> dict[str, Any] | None:
        due_at = _due_time_on_day(
            current,
            self.config.sleep_reminder_time,
            window_minutes=self.config.reminder_due_window_minutes,
        )
        if not due_at:
            return None
        return {
            "priority": 3,
            "reminder_type": "sleep",
            "dedupe_key": f"sleep:{due_at.strftime('%Y-%m-%d')}",
            "message": "睡眠提醒：差不多可以准备睡了。",
        }

    def _water_candidate(self, current: datetime) -> dict[str, Any] | None:
        due_at = None
        for water_time in self.config.water_times:
            due_at = _due_time_on_day(
                current,
                water_time,
                window_minutes=self.config.reminder_due_window_minutes,
            )
            if due_at:
                break
        if not due_at:
            return None
        if self._has_recent_event("water", current, minutes=60):
            return None
        return {
            "priority": 4,
            "reminder_type": "water",
            "dedupe_key": f"water:{due_at.strftime('%Y-%m-%dT%H:%M')}",
            "message": "喝水提醒：白天补点水。",
        }

    def _daily_review_candidate(self, current: datetime) -> dict[str, Any] | None:
        due_at = _due_time_on_day(
            current,
            "00:00",
            window_minutes=self.config.reminder_due_window_minutes,
        )
        if not due_at:
            return None
        review_date = (due_at - timedelta(days=1)).date().isoformat()
        return {
            "priority": 5,
            "reminder_type": "daily_review",
            "dedupe_key": f"daily_review:{review_date}",
            "message": "每日轻复盘：今天的生活记录我会轻量总结，缺失的部分先不追问。",
        }

    def _has_event_on_day(self, event_type: str, current: datetime) -> bool:
        rows = self.repo.conn.execute(
            "SELECT occurred_at FROM life_events WHERE event_type = ?",
            (event_type,),
        ).fetchall()
        return any(_parse_datetime(row["occurred_at"]).date() == current.date() for row in rows)

    def _has_reminder_log_on_day(
        self,
        reminder_type: str,
        status: str,
        current: datetime,
    ) -> bool:
        rows = self.repo.conn.execute(
            """SELECT scheduled_for FROM life_reminder_logs
               WHERE reminder_type = ? AND status = ?""",
            (reminder_type, status),
        ).fetchall()
        return any(_parse_datetime(row["scheduled_for"]).date() == current.date() for row in rows)

    def _latest_exercise_reminder_log(self, current: datetime) -> dict[str, Any] | None:
        rows = self.repo.conn.execute(
            """SELECT * FROM life_reminder_logs
               WHERE reminder_type = 'exercise' AND status IN ('scheduled', 'sending')
               ORDER BY scheduled_for DESC, rowid DESC"""
        ).fetchall()
        for row in rows:
            scheduled = _parse_datetime(row["scheduled_for"])
            if scheduled.date() == current.date():
                return self.repo._reminder_row_to_dict(row)
        return None

    def _has_recent_event(
        self,
        event_type: str,
        current: datetime,
        *,
        minutes: int,
    ) -> bool:
        rows = self.repo.conn.execute(
            "SELECT occurred_at FROM life_events WHERE event_type = ?",
            (event_type,),
        ).fetchall()
        for row in rows:
            occurred = _parse_datetime(row["occurred_at"])
            elapsed_seconds = _seconds_between(occurred, current)
            if 0 <= elapsed_seconds <= minutes * 60:
                return True
        return False


def _recorded(event: dict[str, Any], chat_preview: str) -> dict[str, Any]:
    return {"status": "recorded", "event": event, "chat_preview": chat_preview}


def _rejected(chat_preview: str) -> dict[str, Any]:
    return {"status": "rejected", "event": None, "chat_preview": chat_preview}


def _reminder_updated(chat_preview: str) -> dict[str, Any]:
    return {"status": "reminder_updated", "event": None, "chat_preview": chat_preview}


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def _now() -> datetime:
    return datetime.now().astimezone()


def _fmt_time(value: datetime) -> str:
    return value.strftime("%H:%M")


def _fmt_duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}小时{mins}分钟"
    if hours:
        return f"{hours}小时"
    return f"{mins}分钟"


def _duration_minutes_between(start: datetime, end: datetime) -> int:
    return max(0, round(_seconds_between(start, end) / 60))


def _seconds_between(start: datetime, end: datetime) -> float:
    start, end = _compatible_datetime_pair(start, end)
    return (end - start).total_seconds()


def _compatible_datetime_pair(left: datetime, right: datetime) -> tuple[datetime, datetime]:
    if _is_aware(left) == _is_aware(right):
        return left, right
    return left.replace(tzinfo=None), right.replace(tzinfo=None)


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _due_time_on_day(current: datetime, time_text: str, *, window_minutes: int) -> datetime | None:
    hour, minute = [int(part) for part in time_text.split(":", 1)]
    due_at = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due_at <= current <= due_at + timedelta(minutes=window_minutes):
        return due_at
    return None


def _meal_label(slot: str) -> str:
    return {
        "breakfast": "早餐",
        "lunch": "午餐",
        "dinner": "晚餐",
        "late_snack": "夜宵",
        "snack": "加餐",
    }.get(slot, "饮食")


def _meal_slot_from_text(text: str) -> str:
    for keyword, slot in {
        "早餐": "breakfast",
        "早饭": "breakfast",
        "午餐": "lunch",
        "午饭": "lunch",
        "晚餐": "dinner",
        "晚饭": "dinner",
        "夜宵": "late_snack",
        "加餐": "snack",
    }.items():
        if keyword in text:
            return slot
    return ""
