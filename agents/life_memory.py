"""Deterministic Life Assistant memory analyzer."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from db.life_repo import LifeRepo


class LifeMemoryAnalyzer:
    """Create conservative Life memories from existing records and reminders."""

    def __init__(self, repo: LifeRepo):
        self.repo = repo

    def refresh(self, *, now: str, lookback_days: int = 14) -> dict[str, Any]:
        current = _parse_datetime(now)
        cutoff = current - timedelta(days=lookback_days)
        created_or_updated: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []

        sleep = self._sleep_memory(current, cutoff, lookback_days)
        if sleep:
            observations.append(sleep["observation"])
            created_or_updated.append(sleep["memory"])

        meal_items = self._meal_memories(current, cutoff, lookback_days)
        observations.extend(item["observation"] for item in meal_items)
        created_or_updated.extend(item["memory"] for item in meal_items)

        water = self._water_memory(current, cutoff, lookback_days)
        if water:
            observations.append(water["observation"])
            created_or_updated.append(water["memory"])

        reminder_items = self._reminder_feedback_memories(current, cutoff, lookback_days)
        observations.extend(item["observation"] for item in reminder_items)
        created_or_updated.extend(item["memory"] for item in reminder_items)

        return {
            "observations": len(observations),
            "created_or_updated_items": len(created_or_updated),
            "memory_ids": [item["id"] for item in created_or_updated],
        }

    def _sleep_memory(
        self,
        current: datetime,
        cutoff: datetime,
        lookback_days: int,
    ) -> dict[str, Any] | None:
        rows = self.repo.conn.execute(
            """SELECT * FROM life_events
               WHERE event_type = 'sleep_end'
                 AND status NOT IN ('undone', 'superseded')
                 AND duration_minutes IS NOT NULL
               ORDER BY occurred_at ASC, rowid ASC"""
        ).fetchall()
        events = [
            dict(row)
            for row in rows
            if _in_window(str(row["occurred_at"]), cutoff, current)
            and int(row["duration_minutes"] or 0) > 0
        ]
        if len(events) < 3:
            return None
        if self._has_observation_today("life.sleep", "sleep_pattern", current):
            return None
        avg_minutes = round(sum(int(row["duration_minutes"] or 0) for row in events) / len(events))
        wake_minute = _average_clock_minute([_parse_datetime(str(row["occurred_at"])) for row in events])
        content = (
            f"最近{lookback_days}天有{len(events)}次完整睡眠记录，"
            f"平均睡眠{_fmt_duration(avg_minutes)}，常见醒来时间约{_fmt_clock(wake_minute)}。"
        )
        observation = self.repo.create_memory_observation(
            domain="life.sleep",
            observation_type="sleep_pattern",
            content=f"最近{lookback_days}天有{len(events)}次完整睡眠记录。",
            observed_at=current.isoformat(),
            source="event_stats",
            evidence={
                "event_ids": [str(row["id"]) for row in events],
                "average_minutes": avg_minutes,
                "wake_minute": wake_minute,
            },
        )
        memory = self.repo.upsert_memory_item(
            domain="life.sleep",
            memory_type="pattern",
            status="active" if len(events) >= 5 else "weak",
            content=content,
            confidence="high" if len(events) >= 5 else "medium",
            source="event_stats",
            effective_from=current.isoformat(),
            evidence={"observation_id": observation["id"], **observation["evidence_json"]},
        )
        return {"observation": observation, "memory": memory}

    def _meal_memories(
        self,
        current: datetime,
        cutoff: datetime,
        lookback_days: int,
    ) -> list[dict[str, Any]]:
        rows = self.repo.conn.execute(
            """SELECT * FROM life_events
               WHERE event_type = 'meal'
                 AND status NOT IN ('undone', 'superseded')
               ORDER BY occurred_at ASC, rowid ASC"""
        ).fetchall()
        by_slot: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if not _in_window(str(row["occurred_at"]), cutoff, current):
                continue
            payload = _load_json(row["payload_json"], {})
            if payload.get("meal_time_precision") == "missing":
                continue
            slot = str(payload.get("meal_slot") or "")
            if not slot:
                continue
            by_slot.setdefault(slot, []).append(dict(row))

        result: list[dict[str, Any]] = []
        for slot, events in by_slot.items():
            if len(events) < 3:
                continue
            observation_type = f"meal_pattern:{slot}"
            if self._has_observation_today("life.meal", observation_type, current):
                continue
            average_minute = _average_clock_minute(
                [_parse_datetime(str(row["occurred_at"])) for row in events]
            )
            label = _meal_label(slot)
            content = (
                f"最近{lookback_days}天{label}有{len(events)}次明确时间记录，"
                f"常见时间约{_fmt_clock(average_minute)}。"
            )
            observation = self.repo.create_memory_observation(
                domain="life.meal",
                observation_type=observation_type,
                content=f"最近{lookback_days}天{label}有{len(events)}次明确时间记录。",
                observed_at=current.isoformat(),
                source="event_stats",
                evidence={
                    "meal_slot": slot,
                    "event_ids": [str(row["id"]) for row in events],
                    "average_minute": average_minute,
                },
            )
            memory = self.repo.upsert_memory_item(
                domain="life.meal",
                memory_type="pattern",
                status="active" if len(events) >= 5 else "weak",
                content=content,
                confidence="high" if len(events) >= 5 else "medium",
                source="event_stats",
                effective_from=current.isoformat(),
                evidence={"observation_id": observation["id"], **observation["evidence_json"]},
            )
            result.append({"observation": observation, "memory": memory})
        return result

    def _water_memory(
        self,
        current: datetime,
        cutoff: datetime,
        lookback_days: int,
    ) -> dict[str, Any] | None:
        rows = self.repo.conn.execute(
            """SELECT * FROM life_events
               WHERE event_type = 'water'
                 AND status NOT IN ('undone', 'superseded')
               ORDER BY occurred_at ASC, rowid ASC"""
        ).fetchall()
        counts_by_day: dict[str, int] = {}
        event_ids: list[str] = []
        for row in rows:
            if not _in_window(str(row["occurred_at"]), cutoff, current):
                continue
            day = _parse_datetime(str(row["occurred_at"])).date().isoformat()
            counts_by_day[day] = counts_by_day.get(day, 0) + 1
            event_ids.append(str(row["id"]))
        if len(counts_by_day) < 3:
            return None
        if self._has_observation_today("life.water", "water_pattern", current):
            return None
        total = sum(counts_by_day.values())
        average = round(total / len(counts_by_day), 1)
        content = f"最近{lookback_days}天有{len(counts_by_day)}天记录喝水，平均每天约{average:g}次。"
        observation = self.repo.create_memory_observation(
            domain="life.water",
            observation_type="water_pattern",
            content=f"最近{lookback_days}天有{len(counts_by_day)}天记录喝水。",
            observed_at=current.isoformat(),
            source="event_stats",
            evidence={"event_ids": event_ids, "days": counts_by_day, "average_per_recorded_day": average},
        )
        memory = self.repo.upsert_memory_item(
            domain="life.water",
            memory_type="pattern",
            status="active" if len(counts_by_day) >= 7 else "weak",
            content=content,
            confidence="high" if len(counts_by_day) >= 7 else "medium",
            source="event_stats",
            effective_from=current.isoformat(),
            evidence={"observation_id": observation["id"], **observation["evidence_json"]},
        )
        return {"observation": observation, "memory": memory}

    def _reminder_feedback_memories(
        self,
        current: datetime,
        cutoff: datetime,
        lookback_days: int,
    ) -> list[dict[str, Any]]:
        rows = self.repo.conn.execute(
            """SELECT * FROM life_reminder_logs
               WHERE status = 'failed'
               ORDER BY scheduled_for ASC, rowid ASC"""
        ).fetchall()
        by_type: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if not _in_window(str(row["scheduled_for"]), cutoff, current):
                continue
            by_type.setdefault(str(row["reminder_type"]), []).append(dict(row))

        result: list[dict[str, Any]] = []
        for reminder_type, logs in by_type.items():
            if len(logs) < 3:
                continue
            domain = _domain_for_reminder(reminder_type)
            observation_type = f"reminder_feedback:{reminder_type}"
            if self._has_observation_today(domain, observation_type, current):
                continue
            content = (
                f"最近{lookback_days}天{_reminder_label(reminder_type)}提醒有{len(logs)}次发送失败，"
                "需要避免短时间重复打扰并优先关注发送状态。"
            )
            observation = self.repo.create_memory_observation(
                domain=domain,
                observation_type=observation_type,
                content=f"最近{lookback_days}天{_reminder_label(reminder_type)}提醒发送失败{len(logs)}次。",
                observed_at=current.isoformat(),
                source="reminder_feedback",
                evidence={"reminder_type": reminder_type, "log_ids": [str(row["id"]) for row in logs]},
            )
            memory = self.repo.upsert_memory_item(
                domain=domain,
                memory_type="response",
                status="weak",
                content=content,
                confidence="medium",
                source="reminder_feedback",
                effective_from=current.isoformat(),
                evidence={"observation_id": observation["id"], **observation["evidence_json"]},
            )
            result.append({"observation": observation, "memory": memory})
        return result

    def _has_observation_today(
        self,
        domain: str,
        observation_type: str,
        current: datetime,
    ) -> bool:
        rows = self.repo.conn.execute(
            """SELECT observed_at FROM life_memory_observations
               WHERE domain = ? AND observation_type = ?
               ORDER BY observed_at DESC, rowid DESC
               LIMIT 5""",
            (domain, observation_type),
        ).fetchall()
        current_day = current.date().isoformat()
        for row in rows:
            try:
                if _parse_datetime(str(row["observed_at"])).date().isoformat() == current_day:
                    return True
            except ValueError:
                continue
        return False


def _in_window(value: str, cutoff: datetime, current: datetime) -> bool:
    parsed = _parse_datetime(value)
    parsed, cutoff = _compatible_datetime_pair(parsed, cutoff)
    parsed, current = _compatible_datetime_pair(parsed, current)
    return cutoff <= parsed <= current


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _compatible_datetime_pair(left: datetime, right: datetime) -> tuple[datetime, datetime]:
    if _is_aware(left) == _is_aware(right):
        return left, right
    return left.replace(tzinfo=None), right.replace(tzinfo=None)


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _average_clock_minute(values: list[datetime]) -> int:
    minutes = [value.hour * 60 + value.minute for value in values]
    return round(sum(minutes) / len(minutes))


def _fmt_clock(minute_of_day: int) -> str:
    hour = (minute_of_day // 60) % 24
    minute = minute_of_day % 60
    return f"{hour:02d}:{minute:02d}"


def _fmt_duration(minutes: int) -> str:
    hours = minutes // 60
    rest = minutes % 60
    if hours and rest:
        return f"{hours}小时{rest}分钟"
    if hours:
        return f"{hours}小时"
    return f"{rest}分钟"


def _load_json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _meal_label(slot: str) -> str:
    return {
        "breakfast": "早餐",
        "lunch": "午餐",
        "dinner": "晚餐",
        "snack": "加餐",
    }.get(slot, "饮食")


def _domain_for_reminder(reminder_type: str) -> str:
    return {
        "sleep": "life.sleep",
        "sleep_record": "life.sleep",
        "meal": "life.meal",
        "water": "life.water",
        "sedentary": "life.sedentary",
    }.get(reminder_type, "life.copy")


def _reminder_label(reminder_type: str) -> str:
    return {
        "sleep": "睡眠",
        "sleep_record": "睡眠补记",
        "meal": "饮食",
        "water": "喝水",
        "sedentary": "久坐",
        "exercise": "运动",
    }.get(reminder_type, reminder_type)
