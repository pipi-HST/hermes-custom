"""Read-only Life Dashboard API routes."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date as date_type, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, Query

from agents.life_agent import LifeConfig

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CRON_JOBS_PATH = "/Users/pipi/.hermes/cron/jobs.json"
DEFAULT_RATE_LIMIT_COOLDOWN_MINUTES = 30


def create_life_router(
    conn: sqlite3.Connection | None = None,
    *,
    db_path: str | None = None,
    cron_jobs_path: str | None = None,
    life_log_path: str | None = None,
) -> APIRouter:
    if conn is None and db_path is None:
        raise ValueError("create_life_router requires conn or db_path")

    router = APIRouter()

    @router.get("/life/summary")
    def get_life_summary(date: str = Query(default="")):
        selected_date = date or datetime.now().astimezone().date().isoformat()
        with _life_connection(conn, db_path) as db:
            events = _events_on_date(db, selected_date, limit=10000)
            reminders = _reminders_on_date(db, selected_date, limit=10000)
        sitting_windows = [
            event for event in events
            if event["event_type"] == "sedentary_start" and event.get("status") != "active"
        ]
        active_sitting = next(
            (
                event for event in sorted(
                    events,
                    key=lambda item: item.get("occurred_at", ""),
                    reverse=True,
                )
                if event["event_type"] == "sedentary_start" and event.get("status") == "active"
            ),
            None,
        )
        event_counts: dict[str, int] = {}
        for event in events:
            event_counts[event["event_type"]] = event_counts.get(event["event_type"], 0) + 1
        latest_reminders: dict[str, dict[str, Any]] = {}
        for reminder in reminders:
            reminder_type = reminder["reminder_type"]
            current = latest_reminders.get(reminder_type)
            if not current or reminder["scheduled_for"] > current["scheduled_for"]:
                latest_reminders[reminder_type] = reminder
        return {
            "date": selected_date,
            "metrics": {
                "sitting_total_minutes": sum(int(event.get("duration_minutes") or 0) for event in sitting_windows),
                "sedentary_over_limit_minutes": sum(
                    int(event.get("duration_minutes") or 0)
                    for event in sitting_windows
                    if int(event.get("duration_minutes") or 0) >= 60
                ),
                "event_counts": event_counts,
            },
            "active_sitting": active_sitting,
            "latest_reminders": latest_reminders,
        }

    @router.get("/life/events")
    def list_life_events(
        date: str = Query(default=""),
        limit: int = Query(default=100, le=500),
    ):
        selected_date = date or datetime.now().astimezone().date().isoformat()
        with _life_connection(conn, db_path) as db:
            items = _events_on_date(db, selected_date, limit=limit)
        return {"date": selected_date, "items": items, "total": len(items)}

    @router.get("/life/reminders")
    def list_life_reminders(
        date: str = Query(default=""),
        limit: int = Query(default=100, le=500),
    ):
        selected_date = date or datetime.now().astimezone().date().isoformat()
        with _life_connection(conn, db_path) as db:
            items = _reminders_on_date(db, selected_date, limit=limit)
        return {"date": selected_date, "items": items, "total": len(items)}

    @router.get("/life/daily-status")
    def get_life_daily_status(
        date: str = Query(default=""),
        now: str = Query(default=""),
    ):
        selected_date = date or datetime.now().astimezone().date().isoformat()
        current = _parse_datetime_or_now(now)
        with _life_connection(conn, db_path) as db:
            events = _events_on_date(db, selected_date, limit=10000)
            reminders = _reminders_on_date(db, selected_date, limit=10000)
        items = _daily_status_items(events, reminders, selected_date=selected_date, current=current)
        counts: dict[str, int] = {}
        for item in items:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return {"date": selected_date, "items": items, "counts": counts}

    @router.get("/life/reviews")
    def get_life_reviews(date: str = Query(default="")):
        selected_date = date or datetime.now().astimezone().date().isoformat()
        with _life_connection(conn, db_path) as db:
            daily_review = _daily_review_on_date(db, selected_date)
            weekly_review = _weekly_review_for_date(db, selected_date)
            long_term_decisions = _long_term_decisions(db)
        return {
            "date": selected_date,
            "daily_review": daily_review,
            "weekly_review": weekly_review,
            "long_term_decisions": long_term_decisions,
        }

    @router.get("/life/week")
    def get_life_week(date: str = Query(default="")):
        selected_date = date or datetime.now().astimezone().date().isoformat()
        week_start, week_end = _week_bounds(selected_date)
        days = _date_range(week_start, week_end)
        with _life_connection(conn, db_path) as db:
            events = _events_between(db, week_start, week_end)
            weekly_review = _weekly_review_for_date(db, selected_date)
        trend_rows = [_trend_row_for_day(day, events) for day in days]
        metrics = _weekly_metrics(trend_rows)
        return {
            "date": selected_date,
            "week_start": week_start,
            "week_end": week_end,
            "metrics": metrics,
            "trend_rows": trend_rows,
            "record_groups": _weekly_record_groups(events),
            "weekly_review": weekly_review,
        }

    @router.get("/life/runtime")
    def get_life_runtime(
        now: str = Query(default=""),
        reminder_limit: int = Query(default=12, le=50),
    ):
        current = _parse_datetime_or_now(now)
        resolved_cron_jobs_path = _runtime_path(
            explicit=cron_jobs_path,
            env_name="HERMES_LIFE_CRON_JOBS_PATH",
            default=DEFAULT_CRON_JOBS_PATH,
        )
        resolved_life_log_path = _runtime_path(
            explicit=life_log_path,
            env_name="HERMES_LIFE_LOG_PATH",
            default=str(ROOT / "data" / "life.log"),
        )
        cron = _life_cron_status(resolved_cron_jobs_path, current)
        last_tick = _latest_tick_log(resolved_life_log_path)
        with _life_connection(conn, db_path) as db:
            recent_reminders = _recent_reminder_logs(db, limit=reminder_limit)
            delivery = _delivery_status(db, current=current)
            reminder_rules = _reminder_rules_status(db, current=current)
        return {
            "now": current.isoformat(),
            "cron": cron,
            "delivery": delivery,
            "last_tick": last_tick,
            "recent_reminders": recent_reminders,
            "reminder_rules": reminder_rules,
        }

    @router.get("/life/memories")
    def get_life_memories(
        limit: int = Query(default=50, le=200),
    ):
        with _life_connection(conn, db_path) as db:
            items = _life_memory_items(db, limit=limit)
            observations = _life_memory_observations(db, limit=limit)
            hits = _life_memory_hits(db, limit=limit)
        return {
            "items": items,
            "observations": observations,
            "hits": hits,
        }

    @router.get("/life/decision-traces")
    def get_life_decision_traces(
        date: str = Query(default=""),
        limit: int = Query(default=20, le=100),
    ):
        selected_date = date or datetime.now().astimezone().date().isoformat()
        with _life_connection(conn, db_path) as db:
            items = _decision_traces_on_date(db, selected_date, limit=limit)
        return {"date": selected_date, "items": items, "total": len(items)}

    return router


@contextmanager
def _life_connection(
    conn: sqlite3.Connection | None,
    db_path: str | None,
) -> Iterator[sqlite3.Connection]:
    if db_path is None:
        assert conn is not None
        yield conn
        return

    fresh = sqlite3.connect(db_path)
    fresh.row_factory = sqlite3.Row
    try:
        yield fresh
    finally:
        fresh.close()


def _events_on_date(conn: sqlite3.Connection, selected_date: str, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM life_events
           ORDER BY occurred_at DESC, rowid DESC"""
    ).fetchall()
    items = [
        _event_row_to_dict(row)
        for row in rows
        if _date_key(row["occurred_at"]) == selected_date
        and row["status"] not in {"undone", "superseded"}
    ]
    return items[:limit]


def _reminders_on_date(conn: sqlite3.Connection, selected_date: str, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM life_reminder_logs
           ORDER BY scheduled_for DESC, rowid DESC"""
    ).fetchall()
    items = [
        _reminder_row_to_dict(row)
        for row in rows
        if _date_key(row["scheduled_for"]) == selected_date
    ]
    return items[:limit]


def _events_between(conn: sqlite3.Connection, week_start: str, week_end: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM life_events
           ORDER BY occurred_at DESC, rowid DESC"""
    ).fetchall()
    return [
        _event_row_to_dict(row)
        for row in rows
        if week_start <= _event_life_day(row) <= week_end
        and row["status"] not in {"undone", "superseded"}
    ]


def _daily_review_on_date(conn: sqlite3.Connection, selected_date: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM life_daily_reviews WHERE review_date = ?",
        (selected_date,),
    ).fetchone()
    if not row:
        return None
    return _daily_review_row_to_dict(row)


def _weekly_review_for_date(conn: sqlite3.Connection, selected_date: str) -> dict[str, Any] | None:
    week_start, week_end = _week_bounds(selected_date)
    row = conn.execute(
        """SELECT * FROM life_weekly_reviews
           WHERE week_start = ? AND week_end = ?""",
        (week_start, week_end),
    ).fetchone()
    if not row:
        return None
    return _weekly_review_row_to_dict(row)


def _long_term_decisions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM life_long_term_decisions
           WHERE status IN ('active', 'pending_review_time')
           ORDER BY next_review_at ASC, created_at DESC"""
    ).fetchall()
    return [_decision_row_to_dict(conn, row) for row in rows]


def _event_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["payload_json"] = _load_json(item.get("payload_json"), {})
    return item


def _daily_review_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["metrics_json"] = _load_json(item.get("metrics_json"), {})
    item["notes_json"] = _load_json(item.get("notes_json"), {})
    return item


def _weekly_review_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["labels_json"] = _load_json(item.get("labels_json"), {})
    item["patterns_json"] = _load_json(item.get("patterns_json"), {})
    item["recommendations_json"] = _load_json(item.get("recommendations_json"), [])
    item["evidence_json"] = _load_json(item.get("evidence_json"), [])
    item["delivery_json"] = _load_json(item.get("delivery_json"), {})
    return item


def _decision_row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    review = conn.execute(
        """SELECT * FROM life_decision_reviews
           WHERE decision_id = ?
           ORDER BY reviewed_at DESC, rowid DESC
           LIMIT 1""",
        (item["id"],),
    ).fetchone()
    item["latest_review"] = dict(review) if review else None
    return item


def _reminder_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["payload_json"] = _load_json(item.get("payload_json"), {})
    return item


def _life_memory_items(conn: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM life_memory_items
           ORDER BY status ASC, updated_at DESC, rowid DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [_memory_item_row_to_dict(row) for row in rows]


def _life_memory_observations(conn: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM life_memory_observations
           ORDER BY observed_at DESC, rowid DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [_memory_observation_row_to_dict(row) for row in rows]


def _life_memory_hits(conn: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM life_memory_hits
           ORDER BY used_at DESC, rowid DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [_memory_hit_row_to_dict(row) for row in rows]


def _memory_item_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["evidence_json"] = _load_json(item.get("evidence_json"), {})
    return item


def _memory_observation_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["evidence_json"] = _load_json(item.get("evidence_json"), {})
    return item


def _memory_hit_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["effect_json"] = _load_json(item.get("effect_json"), {})
    return item


def _decision_traces_on_date(conn: sqlite3.Connection, selected_date: str, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM life_decision_traces
           ORDER BY tick_at DESC, rowid DESC
           LIMIT ?""",
        (limit * 3,),
    ).fetchall()
    items = [
        _decision_trace_row_to_dict(row)
        for row in rows
        if _date_key(str(row["tick_at"])) == selected_date
    ]
    return items[:limit]


def _daily_status_items(
    events: list[dict[str, Any]],
    reminders: list[dict[str, Any]],
    *,
    selected_date: str,
    current: datetime,
) -> list[dict[str, Any]]:
    return [
        _sleep_status_item(events),
        _meal_status_item(events),
        _exercise_status_item(events, reminders),
        _water_status_item(events),
        _sedentary_status_item(events, selected_date=selected_date, current=current),
    ]


def _sleep_status_item(events: list[dict[str, Any]]) -> dict[str, Any]:
    sleep_ends = [event for event in events if event["event_type"] == "sleep_end"]
    complete = [event for event in sleep_ends if int(event.get("duration_minutes") or 0) > 0]
    if complete:
        minutes = sum(int(event.get("duration_minutes") or 0) for event in complete)
        latest = max(complete, key=lambda item: str(item.get("occurred_at") or ""))
        return _status_item(
            "sleep",
            "睡眠",
            "done",
            f"已记录 {len(complete)} 段睡眠，共 {_format_minutes_cn(minutes)}",
            f"最近醒来 {str(latest.get('occurred_at') or '')[11:16]}",
        )
    if any(event["event_type"] in {"sleep_start", "sleep_end"} for event in events):
        return _status_item("sleep", "睡眠", "pending", "有睡眠记录，但还缺完整时长", "补一句入睡或醒来时间即可配对。")
    return _status_item("sleep", "睡眠", "pending", "今天还没有完整睡眠记录", "需要补记睡觉和醒来的时间。")


def _meal_status_item(events: list[dict[str, Any]]) -> dict[str, Any]:
    meals = [event for event in events if event["event_type"] == "meal"]
    if not meals:
        return _status_item("meal", "饮食", "pending", "今天还没有饮食记录", "只要记录吃了什么；时间不明确时会追问。")
    missing_time = [
        event for event in meals
        if (event.get("payload_json") or {}).get("meal_time_precision") == "missing"
    ]
    slots = [_meal_slot_cn((event.get("payload_json") or {}).get("meal_slot")) for event in meals]
    summary = f"已记录 {len(meals)} 条饮食：{'、'.join(slots)}"
    if missing_time:
        return _status_item("meal", "饮食", "attention", summary, f"{len(missing_time)} 条还缺具体时间。")
    return _status_item("meal", "饮食", "done", summary, "内容和时间都已留存。")


def _exercise_status_item(events: list[dict[str, Any]], reminders: list[dict[str, Any]]) -> dict[str, Any]:
    exercises = [event for event in events if event["event_type"] == "exercise"]
    if exercises:
        minutes = sum(int(event.get("duration_minutes") or 0) for event in exercises)
        detail = f"累计 {_format_minutes_cn(minutes)}" if minutes else "已记录运动完成。"
        return _status_item("exercise", "运动", "done", f"已记录 {len(exercises)} 次运动", detail)

    plans = [reminder for reminder in reminders if reminder["reminder_type"] == "exercise"]
    skipped = [reminder for reminder in plans if reminder["status"] == "skipped"]
    if skipped:
        return _status_item("exercise", "运动", "done", "今天已标记不练", "运动提醒不会继续追。")
    user_plans = [
        reminder for reminder in plans
        if str(reminder.get("dedupe_key") or "").startswith("exercise_reschedule:")
        or (reminder.get("payload_json") or {}).get("source") == "user_reschedule"
    ]
    if user_plans:
        latest = max(user_plans, key=lambda item: str(item.get("scheduled_for") or ""))
        return _status_item("exercise", "运动", "planned", "今天已有运动安排", f"计划 {str(latest.get('scheduled_for') or '')[11:16]}。")
    if plans:
        latest = max(plans, key=lambda item: str(item.get("scheduled_for") or ""))
        return _status_item("exercise", "运动", "pending", "运动提醒已触发，等待回应或记录", f"最近提醒 {str(latest.get('scheduled_for') or '')[11:16]}。")
    return _status_item("exercise", "运动", "pending", "今天还没有运动记录或安排", "可以直接说几点练、练什么，或说今天不练。")


def _water_status_item(events: list[dict[str, Any]]) -> dict[str, Any]:
    count = sum(1 for event in events if event["event_type"] == "water")
    if count >= 2:
        return _status_item("water", "喝水", "done", f"已记录 {count}/2 次喝水", "达到今天的基础目标。")
    if count == 1:
        return _status_item("water", "喝水", "pending", "已记录 1/2 次喝水", "白天目标还差 1 次。")
    return _status_item("water", "喝水", "pending", "今天还没有喝水记录", "白天会轻提醒两次。")


def _sedentary_status_item(
    events: list[dict[str, Any]],
    *,
    selected_date: str,
    current: datetime,
) -> dict[str, Any]:
    config = LifeConfig()
    sitting_events = [event for event in events if event["event_type"] == "sedentary_start"]
    resolved_minutes = sum(
        int(event.get("duration_minutes") or 0)
        for event in sitting_events
        if event.get("status") != "active"
    )
    over_limit_minutes = sum(
        int(event.get("duration_minutes") or 0)
        for event in sitting_events
        if event.get("status") != "active"
        and int(event.get("duration_minutes") or 0) >= config.sedentary_threshold_minutes
    )
    active = next(
        (
            event for event in sorted(sitting_events, key=lambda item: str(item.get("occurred_at") or ""), reverse=True)
            if event.get("status") == "active"
        ),
        None,
    )
    active_minutes = 0
    if active and selected_date == current.date().isoformat():
        active_minutes = max(0, int((current - _parse_datetime(str(active["occurred_at"]))).total_seconds() // 60))
    total_minutes = resolved_minutes + active_minutes
    if active_minutes >= config.sedentary_threshold_minutes:
        return _status_item(
            "sedentary",
            "久坐",
            "attention",
            f"当前连续坐姿 {_format_minutes_cn(active_minutes)}",
            f"已超过 {config.sedentary_threshold_minutes} 分钟阈值，需要离座 {config.break_threshold_minutes} 分钟解除。",
        )
    if over_limit_minutes:
        return _status_item(
            "sedentary",
            "久坐",
            "attention",
            f"今日久坐超标 {_format_minutes_cn(over_limit_minutes)}",
            f"累计坐姿 {_format_minutes_cn(total_minutes)}。",
        )
    if active:
        return _status_item(
            "sedentary",
            "久坐",
            "planned",
            f"当前连续坐姿 {_format_minutes_cn(active_minutes)}",
            f"未超过 {config.sedentary_threshold_minutes} 分钟阈值。",
        )
    return _status_item("sedentary", "久坐", "done", f"累计坐姿 {_format_minutes_cn(total_minutes)}", "今天暂时没有久坐超标。")


def _status_item(key: str, label: str, status: str, summary: str, detail: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "summary": summary,
        "detail": detail,
    }


def _format_minutes_cn(minutes: int) -> str:
    hours = minutes // 60
    rest = minutes % 60
    if hours and rest:
        return f"{hours}小时{rest}分"
    if hours:
        return f"{hours}小时"
    return f"{rest}分"


def _meal_slot_cn(value: Any) -> str:
    if value == "breakfast":
        return "早餐"
    if value == "lunch":
        return "午饭"
    if value == "dinner":
        return "晚饭"
    if value == "snack":
        return "加餐"
    return "饮食"


def _decision_trace_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["context_json"] = _load_json(item.get("context_json"), {})
    item["candidates_json"] = _load_json(item.get("candidates_json"), [])
    item["memory_influences_json"] = _load_json(item.get("memory_influences_json"), [])
    item["warnings_json"] = _load_json(item.get("warnings_json"), [])
    return item


def _runtime_path(*, explicit: str | None, env_name: str, default: str) -> Path:
    return Path(explicit or os.getenv(env_name) or default)


def _parse_datetime_or_now(value: str) -> datetime:
    if value:
        try:
            return _parse_datetime(value)
        except ValueError:
            pass
    return datetime.now().astimezone()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def _life_cron_status(jobs_path: Path, current: datetime) -> dict[str, Any]:
    job = _load_life_cron_job(jobs_path)
    if not job:
        return {
            "found": False,
            "health": "missing",
            "path": str(jobs_path),
            "enabled": False,
            "state": "missing",
            "schedule_display": "",
            "last_run_at": "",
            "next_run_at": "",
            "last_run_age_seconds": None,
            "last_status": "",
            "last_error": "",
            "last_delivery_error": "",
            "repeat_completed": 0,
        }

    last_run_at = str(job.get("last_run_at") or "")
    last_run_age_seconds = _elapsed_seconds(last_run_at, current)
    health = _cron_health(job, last_run_age_seconds)
    repeat = job.get("repeat") if isinstance(job.get("repeat"), dict) else {}
    return {
        "found": True,
        "health": health,
        "path": str(jobs_path),
        "enabled": bool(job.get("enabled")),
        "state": str(job.get("state") or ""),
        "schedule_display": str(job.get("schedule_display") or ""),
        "last_run_at": last_run_at,
        "next_run_at": str(job.get("next_run_at") or ""),
        "last_run_age_seconds": last_run_age_seconds,
        "last_status": str(job.get("last_status") or ""),
        "last_error": str(job.get("last_error") or ""),
        "last_delivery_error": str(job.get("last_delivery_error") or ""),
        "repeat_completed": int(repeat.get("completed") or 0),
    }


def _load_life_cron_job(jobs_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(jobs_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return None
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if job.get("name") == "life-assistant-tick" or job.get("script") == "life_tick_cron.py":
            return job
    return None


def _cron_health(job: dict[str, Any], last_run_age_seconds: int | None) -> str:
    if not job.get("enabled"):
        return "paused"
    if str(job.get("last_status") or "") == "failed" or job.get("last_error"):
        return "failed"
    if last_run_age_seconds is None:
        return "warning"
    if _is_interval_one_minute(job) and last_run_age_seconds > 5 * 60:
        return "stale"
    return "ok"


def _is_interval_one_minute(job: dict[str, Any]) -> bool:
    schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
    return schedule.get("kind") == "interval" and int(schedule.get("minutes") or 0) <= 1


def _elapsed_seconds(value: str, current: datetime) -> int | None:
    if not value:
        return None
    try:
        parsed = _parse_datetime(value)
    except ValueError:
        return None
    return max(0, int((current - parsed).total_seconds()))


def _latest_tick_log(log_path: Path) -> dict[str, Any] | None:
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    latest: dict[str, Any] | None = None
    latest_time: datetime | None = None
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        try:
            tick_time = _parse_datetime(str(item.get("tick_time") or ""))
        except ValueError:
            continue
        if latest_time is None or tick_time > latest_time:
            latest = item
            latest_time = tick_time
    return latest


def _recent_reminder_logs(conn: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM life_reminder_logs
           ORDER BY scheduled_for DESC, rowid DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [_reminder_row_to_dict(row) for row in rows]


def _delivery_status(conn: sqlite3.Connection, *, current: datetime) -> dict[str, Any]:
    window_cutoff = current - timedelta(hours=6)
    failed = _reminder_failures(conn, since=window_cutoff)
    sending = conn.execute(
        "SELECT count(*) FROM life_reminder_logs WHERE status = 'sending'"
    ).fetchone()[0]
    burst_count = len(_reminder_bursts(conn, since=window_cutoff))
    latest_rate_limit = _latest_rate_limit_failure(conn, current)
    cooldown_until = ""
    cooling_down = False
    if latest_rate_limit:
        scheduled = _parse_datetime(str(latest_rate_limit["scheduled_for"]))
        cooldown_dt = scheduled + timedelta(minutes=DEFAULT_RATE_LIMIT_COOLDOWN_MINUTES)
        cooling_down = current < cooldown_dt
        cooldown_until = cooldown_dt.isoformat()
    status = "rate_limited" if cooling_down else "warning" if failed or sending or burst_count else "ok"
    return {
        "status": status,
        "cooling_down": cooling_down,
        "cooldown_until": cooldown_until,
        "recent_failed_count": len(failed),
        "recent_sending_count": int(sending),
        "recent_burst_count": burst_count,
        "last_failure": failed[-1] if failed else None,
    }


def _reminder_rules_status(conn: sqlite3.Connection, *, current: datetime) -> list[dict[str, Any]]:
    config = LifeConfig()
    day = current.date()
    week_start, week_end = _week_bounds(day.isoformat())
    weekly_due_day = date_type.fromisoformat(week_end)
    rules = [
        _rule_item(
            conn,
            reminder_type="sedentary",
            label="久坐提醒",
            enabled=True,
            schedule_text=(
                f"连续坐姿 {config.sedentary_threshold_minutes} 分钟后触发；之后每 "
                f"{config.sedentary_repeat_minutes} 分钟重复，直到离开 {config.break_threshold_minutes} 分钟"
            ),
            planned_times=[],
            next_due_at="",
            current=current,
            detail="由 Mac 活动和 idle time 判断，不靠回复解除。",
        ),
        _rule_item(
            conn,
            reminder_type="exercise",
            label="运动提醒",
            enabled=True,
            schedule_text=f"每天 {config.exercise_first_time} 起；未回复时每 {config.exercise_repeat_hours} 小时提醒一次",
            planned_times=[_time_on_day(day, config.exercise_first_time, current).isoformat()],
            next_due_at=_next_time_on_or_after(current, day, config.exercise_first_time).isoformat(),
            current=current,
            detail="如果你说今天不练，或指定了运动时间，当天会按你的回复调整。",
        ),
        _rule_item(
            conn,
            reminder_type="water",
            label="喝水提醒",
            enabled=True,
            schedule_text="每天 " + "、".join(config.water_times),
            planned_times=[_time_on_day(day, item, current).isoformat() for item in config.water_times],
            next_due_at=_next_time_from_list(current, day, list(config.water_times)),
            current=current,
            detail="白天两次轻提醒；如果 1 小时内已有喝水记录会跳过。",
        ),
        _rule_item(
            conn,
            reminder_type="meal",
            label="饮食记录",
            enabled=True,
            schedule_text=f"每天 {config.meal_record_check_time} 检查；当天无饮食记录时提醒补记",
            planned_times=[_time_on_day(day, config.meal_record_check_time, current).isoformat()],
            next_due_at=_next_time_on_or_after(current, day, config.meal_record_check_time).isoformat(),
            current=current,
            detail="只在当天完全没有饮食记录时提醒；不会假设你必须吃三顿。",
        ),
        _rule_item(
            conn,
            reminder_type="sleep_record",
            label="睡眠记录",
            enabled=True,
            schedule_text=f"每天 {config.sleep_record_check_time} 检查；缺完整睡眠时长时提醒补记",
            planned_times=[_time_on_day(day, config.sleep_record_check_time, current).isoformat()],
            next_due_at=_next_time_on_or_after(current, day, config.sleep_record_check_time).isoformat(),
            current=current,
            detail="你说“我睡了”和“我醒了”后，系统会配对并计算睡眠时长；缺记录时会追问。",
        ),
        _rule_item(
            conn,
            reminder_type="sleep",
            label="早睡提醒",
            enabled=True,
            schedule_text=f"每天 {config.sleep_reminder_time}",
            planned_times=[_time_on_day(day, config.sleep_reminder_time, current).isoformat()],
            next_due_at=_next_time_on_or_after(current, day, config.sleep_reminder_time).isoformat(),
            current=current,
            detail="轻提醒，不需要回复。",
        ),
        _rule_item(
            conn,
            reminder_type="daily_review",
            label="日复盘",
            enabled=True,
            schedule_text="每天 00:00，复盘前一天",
            planned_times=[_time_on_day(day + timedelta(days=1), "00:00", current).isoformat()],
            next_due_at=_time_on_day(day + timedelta(days=1), "00:00", current).isoformat(),
            current=current,
            detail="生成前一天的生活方式轻复盘。",
        ),
        _rule_item(
            conn,
            reminder_type="weekly_review",
            label="周复盘",
            enabled=True,
            schedule_text="每周日 21:30",
            planned_times=[_time_on_day(weekly_due_day, "21:30", current).isoformat()],
            next_due_at=_time_on_day(weekly_due_day, "21:30", current).isoformat(),
            current=current,
            detail=f"覆盖 {week_start} 至 {week_end}。",
        ),
    ]
    return rules + _long_term_rule_items(conn, current=current)


def _rule_item(
    conn: sqlite3.Connection,
    *,
    reminder_type: str,
    label: str,
    enabled: bool,
    schedule_text: str,
    planned_times: list[str],
    next_due_at: str,
    current: datetime,
    detail: str,
) -> dict[str, Any]:
    latest_log = _latest_rule_log(conn, reminder_type=reminder_type, current=current)
    return {
        "reminder_type": reminder_type,
        "label": label,
        "enabled": enabled,
        "schedule_text": schedule_text,
        "planned_times": planned_times,
        "next_due_at": next_due_at,
        "today_status": str(latest_log.get("status") if latest_log else "not_triggered"),
        "latest_log": latest_log,
        "detail": detail,
    }


def _latest_rule_log(
    conn: sqlite3.Connection,
    *,
    reminder_type: str,
    current: datetime,
) -> dict[str, Any] | None:
    selected_day = current.date().isoformat()
    rows = conn.execute(
        """SELECT * FROM life_reminder_logs
           WHERE reminder_type = ?
           ORDER BY scheduled_for DESC, rowid DESC""",
        (reminder_type,),
    ).fetchall()
    for row in rows:
        item = _reminder_row_to_dict(row)
        if _date_key(str(item["scheduled_for"])) == selected_day:
            return item
        if reminder_type == "daily_review" and item["dedupe_key"] == f"daily_review:{selected_day}":
            return item
    return None


def _long_term_rule_items(conn: sqlite3.Connection, *, current: datetime) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, content, next_review_at, status FROM life_long_term_decisions
           WHERE status IN ('active', 'pending_review_time')
           ORDER BY next_review_at ASC, created_at DESC"""
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        next_review_at = str(row["next_review_at"] or "")
        items.append(
            {
                "reminder_type": "long_term_decision_review",
                "label": "长期决定复盘",
                "enabled": True,
                "schedule_text": f"按决定单独指定：{next_review_at or '待补时间'}",
                "planned_times": [next_review_at] if next_review_at else [],
                "next_due_at": next_review_at,
                "today_status": "not_triggered",
                "latest_log": _latest_long_term_decision_log(conn, str(row["id"])),
                "detail": str(row["content"] or ""),
            }
        )
    return items


def _latest_long_term_decision_log(conn: sqlite3.Connection, decision_id: str) -> dict[str, Any] | None:
    rows = conn.execute(
        """SELECT * FROM life_reminder_logs
           WHERE reminder_type = 'long_term_decision_review'
           ORDER BY scheduled_for DESC, rowid DESC"""
    ).fetchall()
    for row in rows:
        item = _reminder_row_to_dict(row)
        payload = item.get("payload_json") or {}
        if payload.get("decision_id") == decision_id:
            return item
    return None


def _time_on_day(day: date_type, time_text: str, current: datetime) -> datetime:
    hour, minute = [int(part) for part in time_text.split(":", 1)]
    return datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        tzinfo=current.tzinfo,
    )


def _next_time_on_or_after(current: datetime, day: date_type, time_text: str) -> datetime:
    candidate = _time_on_day(day, time_text, current)
    if candidate >= current:
        return candidate
    return _time_on_day(day + timedelta(days=1), time_text, current)


def _next_time_from_list(current: datetime, day: date_type, times: list[str]) -> str:
    for time_text in times:
        candidate = _time_on_day(day, time_text, current)
        if candidate >= current:
            return candidate.isoformat()
    if not times:
        return ""
    return _time_on_day(day + timedelta(days=1), times[0], current).isoformat()


def _reminder_failures(conn: sqlite3.Connection, *, since: datetime) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT reminder_type, scheduled_for, error
           FROM life_reminder_logs
           WHERE status = 'failed'
           ORDER BY scheduled_for ASC, rowid ASC"""
    ).fetchall()
    failures: list[dict[str, Any]] = []
    for row in rows:
        try:
            scheduled = _parse_datetime(str(row["scheduled_for"]))
        except ValueError:
            continue
        if scheduled < since:
            continue
        failures.append(
            {
                "reminder_type": str(row["reminder_type"]),
                "scheduled_for": str(row["scheduled_for"]),
                "error": str(row["error"] or ""),
            }
        )
    return failures


def _latest_rate_limit_failure(conn: sqlite3.Connection, current: datetime) -> dict[str, Any] | None:
    rows = conn.execute(
        """SELECT reminder_type, scheduled_for, error
           FROM life_reminder_logs
           WHERE status = 'failed' AND error LIKE '%rate limited%'
           ORDER BY scheduled_for DESC, rowid DESC"""
    ).fetchall()
    for row in rows:
        error = str(row["error"] or "")
        if not _is_rate_limit_error(error):
            continue
        try:
            scheduled = _parse_datetime(str(row["scheduled_for"]))
        except ValueError:
            continue
        if scheduled <= current:
            return {
                "reminder_type": str(row["reminder_type"]),
                "scheduled_for": str(row["scheduled_for"]),
                "error": error,
            }
    return None


def _reminder_bursts(conn: sqlite3.Connection, *, since: datetime) -> list[dict[str, Any]]:
    min_gap_minutes = {"exercise": 120, "sedentary": LifeConfig().sedentary_repeat_minutes}
    rows = conn.execute(
        """SELECT reminder_type, scheduled_for, status
           FROM life_reminder_logs
           WHERE reminder_type IN ('exercise', 'sedentary')
             AND status IN ('scheduled', 'sending', 'sent')
           ORDER BY reminder_type ASC, scheduled_for ASC, rowid ASC"""
    ).fetchall()
    bursts: list[dict[str, Any]] = []
    previous_by_type: dict[str, str] = {}
    for row in rows:
        reminder_type = str(row["reminder_type"])
        scheduled_for = str(row["scheduled_for"])
        previous = previous_by_type.get(reminder_type)
        previous_by_type[reminder_type] = scheduled_for
        if not previous:
            continue
        current_dt = _parse_datetime(scheduled_for)
        previous_dt = _parse_datetime(previous)
        if current_dt.date() != previous_dt.date() or current_dt < since:
            continue
        gap_minutes = int((current_dt - previous_dt).total_seconds() // 60)
        if gap_minutes < min_gap_minutes[reminder_type]:
            bursts.append(
                {
                    "reminder_type": reminder_type,
                    "previous_scheduled_for": previous,
                    "scheduled_for": scheduled_for,
                    "gap_minutes": gap_minutes,
                    "expected_min_gap_minutes": min_gap_minutes[reminder_type],
                }
            )
    return bursts


def _is_rate_limit_error(error: str) -> bool:
    normalized = error.lower().replace("_", " ").replace("-", " ")
    return "rate limit" in normalized or "too many requests" in normalized


def _load_json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _date_key(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)[:10]
    return parsed.date().isoformat()


def _event_life_day(row: sqlite3.Row) -> str:
    payload = _load_json(row["payload_json"], {})
    if row["event_type"] == "sleep_end" and payload.get("wake_date"):
        return str(payload["wake_date"])
    return _date_key(row["occurred_at"])


def _week_bounds(selected_date: str) -> tuple[str, str]:
    try:
        parsed = date_type.fromisoformat(selected_date)
    except ValueError:
        parsed = datetime.now().astimezone().date()
    start = parsed - timedelta(days=parsed.weekday())
    end = start + timedelta(days=6)
    return start.isoformat(), end.isoformat()


def _date_range(start: str, end: str) -> list[str]:
    current = date_type.fromisoformat(start)
    finish = date_type.fromisoformat(end)
    days: list[str] = []
    while current <= finish:
        days.append(current.isoformat())
        current = current + timedelta(days=1)
    return days


def _trend_row_for_day(day: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    day_events = [event for event in events if _event_dict_life_day(event) == day]
    sleep_minutes = sum(
        int(event.get("duration_minutes") or 0)
        for event in day_events
        if event["event_type"] == "sleep_end"
    )
    exercise_minutes = sum(
        int(event.get("duration_minutes") or 0)
        for event in day_events
        if event["event_type"] == "exercise"
    )
    sitting_total = sum(
        int(event.get("duration_minutes") or 0)
        for event in day_events
        if event["event_type"] == "sedentary_start"
    )
    sedentary_over_limit = sum(
        int(event.get("duration_minutes") or 0)
        for event in day_events
        if event["event_type"] == "sedentary_start"
        and int(event.get("duration_minutes") or 0) >= 60
    )
    return {
        "date": day,
        "sleep_minutes": sleep_minutes,
        "exercise_count": sum(1 for event in day_events if event["event_type"] == "exercise"),
        "exercise_minutes": exercise_minutes,
        "sitting_total_minutes": sitting_total,
        "sedentary_over_limit_minutes": sedentary_over_limit,
        "meal_count": sum(1 for event in day_events if event["event_type"] == "meal"),
        "water_count": sum(1 for event in day_events if event["event_type"] == "water"),
    }


def _weekly_metrics(trend_rows: list[dict[str, Any]]) -> dict[str, Any]:
    sleep_days = sum(1 for row in trend_rows if row["sleep_minutes"])
    total_sleep = sum(int(row["sleep_minutes"]) for row in trend_rows)
    exercise_days = sum(1 for row in trend_rows if row["exercise_count"])
    return {
        "sleep_days": sleep_days,
        "sleep_total_minutes": total_sleep,
        "sleep_average_minutes": round(total_sleep / sleep_days) if sleep_days else 0,
        "exercise_days": exercise_days,
        "exercise_total_minutes": sum(int(row["exercise_minutes"]) for row in trend_rows),
        "sitting_total_minutes": sum(int(row["sitting_total_minutes"]) for row in trend_rows),
        "sedentary_over_limit_minutes": sum(int(row["sedentary_over_limit_minutes"]) for row in trend_rows),
        "meal_count": sum(int(row["meal_count"]) for row in trend_rows),
        "water_count": sum(int(row["water_count"]) for row in trend_rows),
    }


def _weekly_record_groups(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "sleep": [event for event in events if event["event_type"] in {"sleep_start", "sleep_end"}],
        "meal": [event for event in events if event["event_type"] == "meal"],
        "exercise": [event for event in events if event["event_type"] == "exercise"],
        "sedentary": [event for event in events if event["event_type"] in {"sedentary_start", "sedentary_break"}],
        "water": [event for event in events if event["event_type"] == "water"],
    }


def _event_dict_life_day(event: dict[str, Any]) -> str:
    payload = event.get("payload_json") or {}
    if event["event_type"] == "sleep_end" and payload.get("wake_date"):
        return str(payload["wake_date"])
    return _date_key(str(event["occurred_at"]))
