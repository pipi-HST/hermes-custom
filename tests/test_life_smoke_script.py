"""Tests for the Life Assistant smoke-check command."""

from __future__ import annotations

import subprocess
import sys

from scripts.life_smoke import run_smoke


def test_run_life_smoke_checks_core_scenarios(tmp_path) -> None:
    report = run_smoke(db_path=str(tmp_path / "life-smoke.db"))

    assert report["status"] == "passed"
    assert report["summary"]["checked"] >= 10
    assert report["records"]["sleep_duration_minutes"] == 600
    assert report["records"]["sleep_session_duration_minutes"] == 420
    assert report["records"]["planned_exercise_time"] == "2026-05-30T18:00:00+08:00"
    assert report["records"]["exercise_repeat_too_soon"] == ""
    assert report["records"]["exercise_repeat_due"] == "exercise"
    assert report["records"]["sedentary_break_minutes"] == 68
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
