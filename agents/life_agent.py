"""Life Assistant agent core logic."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

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
    exercise_first_time: str = "10:30"
    exercise_repeat_hours: int = 2
    sleep_reminder_time: str = "23:30"
    water_times: tuple[str, str] = ("10:00", "16:00")
    notifications_enabled: bool = False


class LifeAgent:
    def __init__(self, repo: LifeRepo, config: LifeConfig | None = None):
        self.repo = repo
        self.config = config or LifeConfig()

    def record_life_event(
        self,
        message: str,
        *,
        received_at: str | None = None,
    ) -> dict[str, Any]:
        received = _parse_datetime(received_at) if received_at else datetime.now()
        text = message.strip()

        if _is_manual_status_query(text):
            return _rejected("第一版不支持手动查询，我会在定时复盘里总结这些数据。")

        if text == "今天不练了":
            self.repo.create_reminder_log(
                reminder_type="exercise",
                scheduled_for=received.isoformat(),
                status="skipped",
                dedupe_key=f"exercise_skip:{received.date().isoformat()}",
                payload={"reason": "user_skip"},
            )
            return _reminder_updated("今天不练已记录，今天不会继续追运动提醒。")

        scheduled_for = _parse_exercise_reschedule(text, received)
        if scheduled_for:
            self.repo.create_reminder_log(
                reminder_type="exercise",
                scheduled_for=scheduled_for.isoformat(),
                status="scheduled",
                dedupe_key=f"exercise_reschedule:{scheduled_for.isoformat()}",
                payload={"source": "user_reschedule"},
            )
            return {
                **_reminder_updated(f"已把运动提醒改到{_fmt_time(scheduled_for)}。"),
                "scheduled_for": scheduled_for.isoformat(),
            }

        if text == "撤销上一条":
            latest = self.repo.latest_user_event(
                now=received.isoformat(),
                window_minutes=30,
            )
            if not latest:
                return _rejected("没有找到 30 分钟内可撤销的生活记录。")
            updated = self.repo.update_event(latest["id"], status="undone")
            return {"status": "undone", "event": updated, "chat_preview": "已撤销上一条生活记录。"}

        if text in {"我睡了", "睡了", "准备睡了"}:
            event = self.repo.create_event(
                event_type="sleep_start",
                occurred_at=received.isoformat(),
                source="wechat",
                raw_text=message,
            )
            return _recorded(event, f"已记录睡觉时间：{_fmt_time(received)}。")

        if text in {"我醒了", "醒了", "起床了"}:
            start = self._latest_unmatched_sleep_start()
            duration = None
            if start:
                started_at = _parse_datetime(start["occurred_at"])
                duration = max(0, round((received - started_at).total_seconds() / 60))
            event = self.repo.create_event(
                event_type="sleep_end",
                occurred_at=received.isoformat(),
                duration_minutes=duration,
                source="wechat",
                raw_text=message,
                payload={"wake_date": received.date().isoformat()},
            )
            if duration is None:
                return _recorded(event, "已记录醒来时间；还缺睡觉时间，可以补一句大概几点睡。")
            return _recorded(event, f"已记录醒来时间：{_fmt_time(received)}，睡了{_fmt_duration(duration)}。")

        if text in {"已运动", "运动了"}:
            event = self.repo.create_event(
                event_type="exercise",
                occurred_at=received.isoformat(),
                source="wechat",
                raw_text=message,
                status="completed_unknown",
                payload={"completion": "completed_unknown"},
            )
            return _recorded(event, "已记录今天运动完成；可以之后补充类型和时长。")

        exercise = _parse_exercise(text)
        if exercise:
            event = self.repo.create_event(
                event_type="exercise",
                occurred_at=received.isoformat(),
                duration_minutes=exercise["duration_minutes"],
                source="wechat",
                raw_text=message,
                payload={"exercise_type": exercise["exercise_type"]},
            )
            return _recorded(
                event,
                f"已记录运动：{exercise['exercise_type']} {exercise['duration_minutes']} 分钟。",
            )

        meal = _parse_meal(text, received)
        if meal:
            event = self.repo.create_event(
                event_type="meal",
                occurred_at=received.isoformat(),
                source="wechat",
                raw_text=message,
                payload=meal,
            )
            return _recorded(event, f"已记录{_meal_label(meal['meal_slot'])}：{meal['content']}。")

        if _is_water(text):
            event = self.repo.create_event(
                event_type="water",
                occurred_at=received.isoformat(),
                source="wechat",
                raw_text=message,
            )
            return _recorded(event, f"已记录喝水：{_fmt_time(received)}。")

        return _rejected("这条我暂时不能作为生活记录保存。")

    def life_tick(
        self,
        *,
        now: str | None = None,
        activity: ActivitySnapshot | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        current = _parse_datetime(now) if now else datetime.now()
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
                status="scheduled" if dry_run else "sending",
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
        sitting_minutes = max(0, round((current - started_at).total_seconds() / 60))
        if sitting_minutes < self.config.sedentary_threshold_minutes:
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

    def _close_sitting_window(
        self,
        active_window: dict[str, Any],
        current: datetime,
        resolution_source: str,
    ) -> None:
        started_at = _parse_datetime(active_window["occurred_at"])
        duration_minutes = max(0, round((current - started_at).total_seconds() / 60))
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
        scheduled = self._latest_scheduled_exercise(current)
        if scheduled and current < scheduled:
            return None
        return {
            "priority": 2,
            "reminder_type": "exercise",
            "dedupe_key": f"exercise:{current.strftime('%Y-%m-%dT%H:%M')}",
            "message": "运动提醒：今天找个时间动一动吧。回复已运动、今天不练了，或告诉我具体时间。",
        }

    def _sleep_candidate(self, current: datetime) -> dict[str, Any] | None:
        if current.strftime("%H:%M") != self.config.sleep_reminder_time:
            return None
        return {
            "priority": 3,
            "reminder_type": "sleep",
            "dedupe_key": f"sleep:{current.strftime('%Y-%m-%d')}",
            "message": "睡眠提醒：差不多可以准备睡了。",
        }

    def _water_candidate(self, current: datetime) -> dict[str, Any] | None:
        current_time = current.strftime("%H:%M")
        if current_time not in self.config.water_times:
            return None
        if self._has_recent_event("water", current, minutes=60):
            return None
        return {
            "priority": 4,
            "reminder_type": "water",
            "dedupe_key": f"water:{current.strftime('%Y-%m-%dT%H:%M')}",
            "message": "喝水提醒：白天补点水。",
        }

    def _daily_review_candidate(self, current: datetime) -> dict[str, Any] | None:
        if current.strftime("%H:%M") != "00:00":
            return None
        review_date = (current - timedelta(days=1)).date().isoformat()
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

    def _latest_scheduled_exercise(self, current: datetime) -> datetime | None:
        rows = self.repo.conn.execute(
            """SELECT scheduled_for FROM life_reminder_logs
               WHERE reminder_type = 'exercise' AND status = 'scheduled'
               ORDER BY scheduled_for DESC"""
        ).fetchall()
        for row in rows:
            scheduled = _parse_datetime(row["scheduled_for"])
            if scheduled.date() == current.date():
                return scheduled
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
            if 0 <= (current - occurred).total_seconds() <= minutes * 60:
                return True
        return False


def _recorded(event: dict[str, Any], chat_preview: str) -> dict[str, Any]:
    return {"status": "recorded", "event": event, "chat_preview": chat_preview}


def _rejected(chat_preview: str) -> dict[str, Any]:
    return {"status": "rejected", "event": None, "chat_preview": chat_preview}


def _reminder_updated(chat_preview: str) -> dict[str, Any]:
    return {"status": "reminder_updated", "event": None, "chat_preview": chat_preview}


def _parse_exercise(text: str) -> dict[str, Any] | None:
    match = re.search(r"(跑步|健身|快走|散步|骑车|游泳|训练|运动|练)\s*(\d+)\s*(分钟|分|小时)", text)
    if not match:
        return None
    duration = int(match.group(2))
    if match.group(3) == "小时":
        duration *= 60
    return {"exercise_type": match.group(1), "duration_minutes": duration}


def _parse_exercise_reschedule(text: str, received: datetime) -> datetime | None:
    if "提醒我运动" not in text:
        return None
    match = re.search(r"(上午|中午|下午|晚上)?\s*(\d{1,2})\s*点", text)
    if not match:
        return None
    hour = int(match.group(2))
    period = match.group(1) or ""
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    return received.replace(hour=hour, minute=0, second=0, microsecond=0)


def _parse_meal(text: str, received: datetime) -> dict[str, Any] | None:
    explicit_slots = {
        "早饭": "breakfast",
        "早餐": "breakfast",
        "午饭": "lunch",
        "午餐": "lunch",
        "晚饭": "dinner",
        "晚餐": "dinner",
        "夜宵": "late_snack",
        "加餐": "snack",
    }
    slot = ""
    for keyword, value in explicit_slots.items():
        if keyword in text:
            slot = value
            break
    if "吃" not in text and not slot:
        return None
    content = re.sub(r"^(早饭|早餐|午饭|午餐|晚饭|晚餐|夜宵|加餐)?\s*吃了?", "", text).strip()
    if not content:
        return None
    inferred = False
    if not slot:
        slot = _infer_meal_slot(received)
        inferred = True
    return {"meal_slot": slot, "meal_slot_inferred": inferred, "content": content}


def _infer_meal_slot(received: datetime) -> str:
    minutes = received.hour * 60 + received.minute
    if 5 * 60 <= minutes < 10 * 60 + 30:
        return "breakfast"
    if 10 * 60 + 30 <= minutes < 15 * 60:
        return "lunch"
    if 17 * 60 <= minutes < 22 * 60:
        return "dinner"
    if minutes >= 22 * 60 or minutes < 5 * 60:
        return "late_snack"
    return "snack"


def _is_water(text: str) -> bool:
    return text in {"喝水", "喝水了", "喝了水", "饮水"}


def _is_manual_status_query(text: str) -> bool:
    return any(keyword in text for keyword in ("坐了多久", "状态怎么样", "今天状态"))


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fmt_time(value: datetime) -> str:
    return value.strftime("%H:%M")


def _fmt_duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}小时{mins}分钟"
    if hours:
        return f"{hours}小时"
    return f"{mins}分钟"


def _meal_label(slot: str) -> str:
    return {
        "breakfast": "早餐",
        "lunch": "午餐",
        "dinner": "晚餐",
        "late_snack": "夜宵",
        "snack": "加餐",
    }.get(slot, "饮食")
