#!/usr/bin/env python3
"""Run deterministic Life Assistant smoke scenarios."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.life_agent import ActivitySnapshot, LifeAgent
from db.life_repo import LifeRepo
from db.life_schema import init_life_db


def run_smoke(*, db_path: str | None = None) -> dict[str, Any]:
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if db_path is None:
        temp_dir = tempfile.TemporaryDirectory(prefix="hermes-life-smoke-")
        db_path = str(Path(temp_dir.name) / "life-smoke.db")
    try:
        agent = LifeAgent(LifeRepo(init_life_db(db_path)))
        checks: list[dict[str, Any]] = []
        records: dict[str, Any] = {}

        _recording_scenarios(agent, checks, records)
        _sedentary_scenario(agent, checks, records)
        _reminder_scenarios(agent, checks, records)

        failed = [check for check in checks if not check["passed"]]
        return {
            "status": "failed" if failed else "passed",
            "summary": {
                "checked": len(checks),
                "failed": len(failed),
                "db_path": db_path,
            },
            "records": records,
            "failed_checks": failed,
        }
    finally:
        if temp_dir:
            temp_dir.cleanup()


def _recording_scenarios(
    agent: LifeAgent,
    checks: list[dict[str, Any]],
    records: dict[str, Any],
) -> None:
    wake = agent.record_life_event(
        "中午13点半醒的",
        received_at="2026-05-30T17:35:54+08:00",
    )
    sleep = agent.record_life_event(
        "今天凌晨三点半睡的",
        received_at="2026-05-30T17:36:41+08:00",
    )
    meal = agent.record_life_event(
        "午饭吃了腰果饼干",
        received_at="2026-05-30T12:20:00+08:00",
    )
    meal_modify = agent.record_life_event(
        "上一条改成加餐",
        received_at="2026-05-30T12:30:00+08:00",
    )
    water = agent.record_life_event(
        "喝水了",
        received_at="2026-05-30T16:00:00+08:00",
    )
    exercise = agent.record_life_event(
        "跑步 30 分钟",
        received_at="2026-05-30T19:00:00+08:00",
    )
    sleep_session = agent.record_life_event(
        "昨晚1点睡，今天8点醒",
        received_at="2026-05-30T09:00:00+08:00",
    )
    wake_event = agent.repo.get_event(wake["event"]["id"])

    records["sleep_duration_minutes"] = wake_event["duration_minutes"]
    records["sleep_session_duration_minutes"] = sleep_session["event"]["duration_minutes"]
    records["meal_content"] = meal["event"]["payload_json"].get("content")
    records["modified_meal_slot"] = meal_modify["event"]["payload_json"].get("meal_slot")
    records["water_event_type"] = water["event"]["event_type"]
    records["exercise_duration_minutes"] = exercise["event"]["duration_minutes"]

    _check(checks, "wake record is stored", wake["event"]["event_type"] == "sleep_end")
    _check(checks, "sleep start record is stored", sleep["event"]["event_type"] == "sleep_start")
    _check(checks, "sleep duration backfills", wake_event["duration_minutes"] == 600)
    _check(checks, "sleep session duration is stored", sleep_session["event"]["duration_minutes"] == 420)
    _check(checks, "meal content is stored", meal["event"]["payload_json"].get("content") == "腰果饼干")
    _check(checks, "latest meal can be modified", meal_modify["status"] == "modified")
    _check(checks, "latest meal slot modification is stored", records["modified_meal_slot"] == "snack")
    _check(checks, "water record is stored", water["event"]["event_type"] == "water")
    _check(checks, "exercise duration is stored", exercise["event"]["duration_minutes"] == 30)


def _sedentary_scenario(
    agent: LifeAgent,
    checks: list[dict[str, Any]],
    records: dict[str, Any],
) -> None:
    first_tick = agent.life_tick(
        now="2026-05-30T09:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=10, available=True),
        dry_run=True,
    )
    alert_tick = agent.life_tick(
        now="2026-05-30T10:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=30, available=True),
        dry_run=True,
    )
    break_tick = agent.life_tick(
        now="2026-05-30T10:20:00+08:00",
        activity=ActivitySnapshot(idle_seconds=360, available=True),
        dry_run=True,
    )
    reply = agent.record_life_event(
        "已活动",
        received_at="2026-05-30T10:09:00+08:00",
    )
    break_row = agent.repo.conn.execute(
        "SELECT * FROM life_events WHERE event_type = 'sedentary_break'"
    ).fetchone()

    records["sedentary_alert_type"] = (alert_tick["selected"] or {}).get("reminder_type", "")
    records["sedentary_break_minutes"] = int(break_row["duration_minutes"] if break_row else -1)
    records["sedentary_reply_status"] = reply["status"]

    _check(checks, "sedentary start does not alert early", first_tick["selected"] is None)
    _check(
        checks,
        "sedentary alert wins after threshold",
        (alert_tick["selected"] or {}).get("reminder_type") == "sedentary",
    )
    _check(checks, "sedentary break clears alert", break_tick["selected"] is None)
    _check(checks, "sedentary break duration is stored", records["sedentary_break_minutes"] == 79)
    _check(checks, "sedentary reply does not create event", reply["status"] == "rejected" and reply["event"] is None)


def _reminder_scenarios(
    agent: LifeAgent,
    checks: list[dict[str, Any]],
    records: dict[str, Any],
) -> None:
    planned = agent.record_life_event(
        "我打算下午6点练八段锦。",
        received_at="2026-05-30T17:37:49+08:00",
    )
    skip = agent.record_life_event(
        "今天不练了",
        received_at="2026-05-31T10:45:00+08:00",
    )
    query = agent.record_life_event(
        "我今天坐了多久？",
        received_at="2026-05-31T18:00:00+08:00",
    )
    health = agent.record_life_event(
        "健康：我最近睡不好，是不是身体有问题？",
        received_at="2026-05-31T19:00:00+08:00",
    )
    sedentary_pause = agent.record_life_event(
        "暂停久坐警报 2 小时",
        received_at="2026-05-31T10:45:00+08:00",
    )
    offset_water_tick = agent.life_tick(
        now="2026-05-28T10:09:20+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )
    offset_daily_review_tick = agent.life_tick(
        now="2026-05-27T00:09:20+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )
    weekly_review_tick = agent.life_tick(
        now="2026-05-31T21:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )
    first_exercise_tick = agent.life_tick(
        now="2026-05-28T10:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )
    too_soon_exercise_tick = agent.life_tick(
        now="2026-05-28T11:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )
    due_exercise_tick = agent.life_tick(
        now="2026-05-28T12:30:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    records["planned_exercise_time"] = planned.get("scheduled_for", "")
    records["exercise_skip_status"] = skip["status"]
    records["manual_query_status"] = query["status"]
    records["health_consultation_status"] = health["status"]
    records["sedentary_pause_until"] = sedentary_pause.get("scheduled_for", "")
    records["offset_water_reminder"] = (offset_water_tick["selected"] or {}).get("reminder_type", "")
    records["offset_daily_review"] = (offset_daily_review_tick["selected"] or {}).get("reminder_type", "")
    records["daily_review_rows"] = agent.repo.conn.execute(
        "SELECT count(*) AS cnt FROM life_daily_reviews"
    ).fetchone()["cnt"]
    records["weekly_review"] = (weekly_review_tick["selected"] or {}).get("reminder_type", "")
    records["weekly_review_rows"] = agent.repo.conn.execute(
        "SELECT count(*) AS cnt FROM life_weekly_reviews"
    ).fetchone()["cnt"]
    records["exercise_repeat_first"] = (first_exercise_tick["selected"] or {}).get("reminder_type", "")
    records["exercise_repeat_too_soon"] = (too_soon_exercise_tick["selected"] or {}).get("reminder_type", "")
    records["exercise_repeat_due"] = (due_exercise_tick["selected"] or {}).get("reminder_type", "")

    _check(checks, "planned exercise schedules reminder", planned["status"] == "reminder_updated")
    _check(checks, "planned exercise time is stored", planned.get("scheduled_for") == "2026-05-30T18:00:00+08:00")
    _check(checks, "exercise skip updates reminder state", skip["status"] == "reminder_updated")
    _check(checks, "manual query is not recorded", query["status"] == "rejected" and query["event"] is None)
    _check(checks, "health consultation is not recorded", health["status"] == "rejected" and health["event"] is None)
    _check(checks, "sedentary pause stores pause-until time", sedentary_pause.get("scheduled_for") == "2026-05-31T12:45:00+08:00")
    _check(checks, "water reminder tolerates cron offset", records["offset_water_reminder"] == "water")
    _check(checks, "daily review tolerates cron offset", records["offset_daily_review"] == "daily_review")
    _check(checks, "daily review is stored", records["daily_review_rows"] >= 1)
    _check(checks, "weekly review can run", records["weekly_review"] == "weekly_review")
    _check(checks, "weekly review is stored", records["weekly_review_rows"] >= 1)
    _check(checks, "exercise reminder starts", records["exercise_repeat_first"] == "exercise")
    _check(checks, "exercise reminder does not repeat too soon", records["exercise_repeat_too_soon"] == "")
    _check(checks, "exercise reminder repeats after interval", records["exercise_repeat_due"] == "exercise")


def _check(checks: list[dict[str, Any]], name: str, passed: bool) -> None:
    checks.append({"name": name, "passed": bool(passed)})


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Life Assistant smoke scenarios.")
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional SQLite DB path. Defaults to an isolated temporary DB.",
    )
    args = parser.parse_args()
    report = run_smoke(db_path=args.db_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
