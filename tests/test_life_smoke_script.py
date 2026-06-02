"""Tests for the Life Assistant smoke-check command."""

from __future__ import annotations

import subprocess
import sys
import json

from bridge import life_bridge
from db.life_repo import LifeRepo
from db.life_schema import init_life_db
from scripts import life_tick_cron
from scripts.life_acceptance import run_acceptance
from scripts.life_audit import run_audit
from scripts.life_repair import apply_repairs, plan_repairs
from scripts.life_smoke import run_smoke


def test_run_life_smoke_checks_core_scenarios(tmp_path) -> None:
    report = run_smoke(db_path=str(tmp_path / "life-smoke.db"))

    assert report["status"] == "passed"
    assert report["summary"]["checked"] >= 10
    assert report["records"]["sleep_duration_minutes"] == 600
    assert report["records"]["sleep_session_duration_minutes"] == 420
    assert report["records"]["modified_meal_slot"] == "snack"
    assert report["records"]["planned_exercise_time"] == "2026-05-30T18:00:00+08:00"
    assert report["records"]["offset_water_reminder"] == "water"
    assert report["records"]["offset_daily_review"] == "daily_review"
    assert report["records"]["exercise_repeat_too_soon"] == ""
    assert report["records"]["exercise_repeat_due"] == "exercise"
    assert report["records"]["sedentary_break_minutes"] == 75
    assert report["records"]["sedentary_reply_status"] == "rejected"
    assert report["records"]["health_consultation_status"] == "rejected"
    assert report["records"]["sedentary_pause_until"] == "2026-05-31T12:45:00+08:00"


def test_life_smoke_cli_outputs_summary(tmp_path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/life_smoke.py",
            "--db-path",
            str(tmp_path / "life-smoke-cli.db"),
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert '"status": "passed"' in result.stdout
    assert '"sleep_duration_minutes": 600' in result.stdout


def test_life_acceptance_reports_live_pipeline_sections(tmp_path) -> None:
    db_path = str(tmp_path / "life-acceptance.db")
    repo = LifeRepo(init_life_db(db_path))
    repo.create_event(
        event_type="sleep_end",
        occurred_at="2026-06-01T08:00:00+08:00",
        duration_minutes=420,
        source="wechat",
        payload={"wake_date": "2026-06-01"},
    )
    repo.create_event(
        event_type="meal",
        occurred_at="2026-06-01T12:30:00+08:00",
        source="wechat",
        raw_text="午饭吃了牛肉饭",
        payload={"meal_slot": "lunch", "content": "牛肉饭", "meal_time_precision": "exact"},
    )
    repo.create_event(
        event_type="water",
        occurred_at="2026-06-01T10:00:00+08:00",
        source="wechat",
    )
    repo.create_event(
        event_type="exercise",
        occurred_at="2026-06-01T18:00:00+08:00",
        duration_minutes=30,
        source="wechat",
    )
    repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-06-01T09:00:00+08:00",
        ended_at="2026-06-01T09:45:00+08:00",
        duration_minutes=45,
        source="mac_activity",
        status="auto_resolved",
    )
    repo.create_reminder_log(
        reminder_type="water",
        scheduled_for="2026-06-01T10:00:00+08:00",
        status="sent",
        sent_at="2026-06-01T10:00:05+08:00",
        dedupe_key="water:2026-06-01T10:00",
    )
    repo.create_daily_review(
        review_date="2026-06-01",
        summary="记录完整。",
        metrics={"sleep_minutes": 420},
        notes={},
    )
    repo.create_weekly_review(
        week_start="2026-06-01",
        week_end="2026-06-07",
        summary="本周开始记录。",
        labels={},
        patterns={},
        recommendations=[],
    )

    report = run_acceptance(
        db_path=db_path,
        now="2026-06-01T22:00:00+08:00",
        api_base="",
        dashboard_url="",
    )

    assert report["status"] == "passed"
    assert report["sections"]["live_database"]["status"] == "passed"
    assert report["sections"]["pipeline_coverage"]["status"] == "passed"
    check_names = {check["name"] for check in report["checks"]}
    assert {
        "sleep_records_exist",
        "meal_records_exist",
        "water_records_exist",
        "exercise_records_exist",
        "reminder_logs_exist",
        "daily_reviews_exist",
        "weekly_reviews_exist",
    }.issubset(check_names)


def test_life_repair_dry_run_and_apply_stale_sending_reminder(tmp_path) -> None:
    db_path = str(tmp_path / "life-repair.db")
    repo = LifeRepo(init_life_db(db_path))
    reminder = repo.create_reminder_log(
        reminder_type="daily_review",
        scheduled_for="2026-06-02T00:00:52+08:00",
        status="sending",
        dedupe_key="daily_review:2026-06-01",
    )

    planned = plan_repairs(
        db_path=db_path,
        completed_at="2026-06-02T00:00:53+08:00",
        delivery_error="",
    )
    row_after_plan = repo.conn.execute(
        "SELECT status FROM life_reminder_logs WHERE id = ?",
        (reminder["id"],),
    ).fetchone()

    assert planned["summary"]["action_count"] == 1
    assert planned["actions"][0]["kind"] == "stale_sending_reminder"
    assert row_after_plan["status"] == "sending"

    applied = apply_repairs(
        db_path=db_path,
        completed_at="2026-06-02T00:00:53+08:00",
        delivery_error="",
    )
    row_after_apply = repo.conn.execute(
        "SELECT status, sent_at, error FROM life_reminder_logs WHERE id = ?",
        (reminder["id"],),
    ).fetchone()

    assert applied["summary"]["applied_count"] == 1
    assert row_after_apply["status"] == "sent"
    assert row_after_apply["sent_at"] == "2026-06-02T00:00:53+08:00"
    assert row_after_apply["error"] == ""


def test_life_repair_can_apply_superseded_duplicate_reminder(tmp_path) -> None:
    db_path = str(tmp_path / "life-repair.db")
    repo = LifeRepo(init_life_db(db_path))
    duplicate = repo.create_reminder_log(
        reminder_type="exercise",
        scheduled_for="2026-06-02T10:30:00+08:00",
        status="scheduled",
        dedupe_key="exercise:2026-06-02T10:30:duplicate",
    )
    repo.create_reminder_log(
        reminder_type="exercise",
        scheduled_for="2026-06-02T10:30:00+08:00",
        status="sent",
        sent_at="2026-06-02T10:31:00+08:00",
        dedupe_key="exercise:2026-06-02T10:30",
    )

    planned = plan_repairs(db_path=db_path)

    assert planned["summary"]["safe_apply_count"] == 1
    assert planned["actions"][0]["kind"] == "superseded_duplicate_reminder"
    assert planned["actions"][0]["id"] == duplicate["id"]

    applied = apply_repairs(db_path=db_path)
    row_after_apply = repo.conn.execute(
        "SELECT status FROM life_reminder_logs WHERE id = ?",
        (duplicate["id"],),
    ).fetchone()

    assert applied["summary"]["applied_count"] == 1
    assert row_after_apply["status"] == "superseded"


def test_life_audit_reports_runtime_warning_without_writing(tmp_path) -> None:
    db_path = str(tmp_path / "life-audit.db")
    repo = LifeRepo(init_life_db(db_path))
    repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-30T09:00:00+08:00",
        source="mac_activity",
        status="active",
    )
    repo.create_reminder_log(
        reminder_type="sedentary",
        scheduled_for="2026-05-30T23:00:00+08:00",
        status="failed",
        dedupe_key="sedentary:2026-05-30T23:00",
    )

    report = run_audit(db_path=db_path, now="2026-05-30T23:30:00+08:00")

    assert report["status"] == "warning"
    assert report["summary"]["active_sitting_windows"] == 1
    assert report["summary"]["failed_reminders"] == 1
    assert any(check["name"] == "active_sitting_window_age" for check in report["checks"])


def test_life_audit_fails_when_multiple_active_sitting_windows(tmp_path) -> None:
    db_path = str(tmp_path / "life-audit.db")
    repo = LifeRepo(init_life_db(db_path))
    repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-30T09:00:00+08:00",
        source="mac_activity",
        status="active",
    )
    repo.create_event(
        event_type="sedentary_start",
        occurred_at="2026-05-30T10:00:00+08:00",
        source="mac_activity",
        status="active",
    )

    report = run_audit(db_path=db_path, now="2026-05-30T10:10:00+08:00")

    assert report["status"] == "failed"
    assert report["summary"]["active_sitting_windows"] == 2


def test_life_audit_warns_on_duplicate_sleep_sessions(tmp_path) -> None:
    db_path = str(tmp_path / "life-audit.db")
    repo = LifeRepo(init_life_db(db_path))
    for raw_text in ("凌晨三点半睡，中午13点半醒", "今天凌晨三点半睡的，中午13点半醒的"):
        repo.create_event(
            event_type="sleep_end",
            occurred_at="2026-05-31T13:30:00+08:00",
            duration_minutes=600,
            source="wechat",
            raw_text=raw_text,
            payload={"wake_date": "2026-05-31"},
        )

    report = run_audit(db_path=db_path, now="2026-05-31T21:00:00+08:00")

    assert report["status"] == "warning"
    assert report["summary"]["duplicate_sleep_sessions"] == 1
    assert any(check["name"] == "duplicate_sleep_sessions" for check in report["checks"])


def test_life_audit_warns_on_dirty_meal_records(tmp_path) -> None:
    db_path = str(tmp_path / "life-audit.db")
    repo = LifeRepo(init_life_db(db_path))
    repo.create_event(
        event_type="meal",
        occurred_at="2026-06-01T07:00:00+08:00",
        source="wechat",
        raw_text="晚饭吃了馄饨，大概七点吃的",
        payload={
            "meal_slot": "dinner",
            "meal_slot_inferred": False,
            "content": "馄饨，大概",
            "meal_time_precision": "exact",
        },
    )

    report = run_audit(db_path=db_path, now="2026-06-01T22:00:00+08:00")

    assert report["status"] == "warning"
    assert report["summary"]["meal_quality_issues_count"] == 1
    assert report["summary"]["meal_quality_issues"][0]["flags"] == [
        "content_time_tail",
        "dinner_morning_time",
    ]
    assert any(check["name"] == "meal_record_quality" for check in report["checks"])


def test_life_audit_ignores_historical_failed_reminders_and_bursts(tmp_path) -> None:
    db_path = str(tmp_path / "life-audit.db")
    repo = LifeRepo(init_life_db(db_path))
    repo.create_reminder_log(
        reminder_type="sedentary",
        scheduled_for="2026-05-31T08:00:00+08:00",
        status="failed",
        dedupe_key="sedentary:2026-05-31T08:00",
    )
    repo.create_reminder_log(
        reminder_type="exercise",
        scheduled_for="2026-05-31T08:01:00+08:00",
        status="sent",
        sent_at="2026-05-31T08:01:01+08:00",
        dedupe_key="exercise:2026-05-31T08:01",
    )
    repo.create_reminder_log(
        reminder_type="exercise",
        scheduled_for="2026-05-31T08:02:00+08:00",
        status="sent",
        sent_at="2026-05-31T08:02:01+08:00",
        dedupe_key="exercise:2026-05-31T08:02",
    )

    report = run_audit(db_path=db_path, now="2026-05-31T23:00:00+08:00")

    assert report["status"] == "ok"
    assert report["summary"]["failed_reminders"] == 0
    assert report["summary"]["historical_failed_reminders"] == 1
    assert report["summary"]["reminder_bursts"] == []
    assert report["summary"]["historical_reminder_bursts"] == 1


def test_life_audit_warns_on_recent_successful_reminder_burst(tmp_path) -> None:
    db_path = str(tmp_path / "life-audit.db")
    repo = LifeRepo(init_life_db(db_path))
    repo.create_reminder_log(
        reminder_type="exercise",
        scheduled_for="2026-05-31T14:51:00+08:00",
        status="sent",
        sent_at="2026-05-31T14:51:01+08:00",
        dedupe_key="exercise:2026-05-31T14:51",
    )
    repo.create_reminder_log(
        reminder_type="exercise",
        scheduled_for="2026-05-31T14:52:00+08:00",
        status="sent",
        sent_at="2026-05-31T14:52:01+08:00",
        dedupe_key="exercise:2026-05-31T14:52",
    )

    report = run_audit(db_path=db_path, now="2026-05-31T15:00:00+08:00")

    assert report["status"] == "warning"
    assert report["summary"]["reminder_bursts"][0]["reminder_type"] == "exercise"
    assert report["summary"]["reminder_bursts"][0]["gap_minutes"] == 1
    assert any(check["name"] == "reminder_bursts" for check in report["checks"])


def test_life_audit_ignores_user_rescheduled_exercise_plan_as_burst(tmp_path) -> None:
    db_path = str(tmp_path / "life-audit.db")
    repo = LifeRepo(init_life_db(db_path))
    repo.create_reminder_log(
        reminder_type="exercise",
        scheduled_for="2026-06-01T22:00:00+08:00",
        status="scheduled",
        dedupe_key="exercise_reschedule:2026-06-01T22:00:00+08:00",
        payload={"source": "user_reschedule"},
    )
    repo.create_reminder_log(
        reminder_type="exercise",
        scheduled_for="2026-06-01T22:00:18+08:00",
        status="sent",
        sent_at="2026-06-01T22:01:18+08:00",
        dedupe_key="exercise:2026-06-01T22:00",
    )

    report = run_audit(db_path=db_path, now="2026-06-01T23:00:00+08:00")

    assert report["status"] == "ok"
    assert report["summary"]["reminder_bursts"] == []


def test_life_tick_cron_outputs_one_sleep_reminder_per_day(tmp_path, monkeypatch, capsys) -> None:
    db_path = str(tmp_path / "life-cron.db")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "database:",
                f"  path: {db_path}",
                "life:",
                "  notifications_enabled: true",
            ]
        ),
        encoding="utf-8",
    )
    repo = LifeRepo(init_life_db(db_path))
    repo.create_event(
        event_type="exercise",
        occurred_at="2026-05-30T18:00:00+08:00",
        source="wechat",
        status="completed_unknown",
    )

    times = iter(["2026-05-30T23:30:00+08:00", "2026-05-30T23:31:00+08:00"])

    monkeypatch.setattr(life_bridge, "_agent", None)
    monkeypatch.setattr(life_bridge, "_db_path", None)
    monkeypatch.setattr(life_bridge, "_config_path", config_path)
    monkeypatch.setattr(
        life_bridge,
        "_read_mac_activity",
        lambda: life_bridge.ActivitySnapshot(idle_seconds=None, available=False),
    )
    monkeypatch.setattr(
        life_tick_cron,
        "life_tick",
        lambda dry_run=False: life_bridge.life_tick(now=next(times), dry_run=dry_run),
    )

    first_status = life_tick_cron.main()
    first_output = capsys.readouterr().out
    second_status = life_tick_cron.main()
    second_output = capsys.readouterr().out

    assert first_status == 0
    assert "睡眠提醒" in first_output
    assert second_status == 0
    assert second_output == ""


def test_life_tick_cron_reconciles_previous_delivery_result(tmp_path, monkeypatch, capsys) -> None:
    db_path = str(tmp_path / "life-cron.db")
    jobs_path = tmp_path / "jobs.json"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "database:",
                f"  path: {db_path}",
                "life:",
                "  notifications_enabled: true",
            ]
        ),
        encoding="utf-8",
    )
    jobs_path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "life-test",
                        "name": "life-assistant-tick",
                        "last_run_at": "2026-05-30T10:00:30+08:00",
                        "last_delivery_error": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    repo = LifeRepo(init_life_db(db_path))
    repo.create_reminder_log(
        reminder_type="water",
        scheduled_for="2026-05-30T10:00:00+08:00",
        status="sending",
        dedupe_key="water:2026-05-30T10:00",
    )

    monkeypatch.setenv("HERMES_LIFE_CRON_JOBS_PATH", str(jobs_path))
    monkeypatch.setattr(life_bridge, "_agent", None)
    monkeypatch.setattr(life_bridge, "_db_path", None)
    monkeypatch.setattr(life_bridge, "_config_path", config_path)
    monkeypatch.setattr(
        life_tick_cron,
        "life_tick",
        lambda dry_run=False: {"status": "success", "chat_preview": "", "meta": {"should_notify": False}},
    )

    status = life_tick_cron.main()
    output = capsys.readouterr().out

    row = repo.conn.execute(
        "SELECT * FROM life_reminder_logs WHERE dedupe_key = 'water:2026-05-30T10:00'"
    ).fetchone()
    assert status == 0
    assert output == ""
    assert row["status"] == "sent"
