#!/usr/bin/env python3
"""Run one Life Assistant tick for Hermes cron delivery."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.life_bridge import life_tick, reconcile_life_delivery


def main() -> int:
    _reconcile_previous_delivery()
    envelope = life_tick(dry_run=False)
    if envelope.get("status") != "success":
        print(envelope.get("chat_preview", "Life Assistant tick failed."))
        return 1
    if envelope.get("meta", {}).get("should_notify"):
        print(envelope.get("chat_preview", ""))
    return 0


def _reconcile_previous_delivery() -> None:
    job = _load_life_cron_job()
    if not job:
        return
    completed_at = str(job.get("last_run_at") or "")
    if not completed_at:
        return
    delivery_error = str(job.get("last_delivery_error") or "")
    reconcile_life_delivery(completed_at=completed_at, delivery_error=delivery_error)


def _load_life_cron_job() -> dict | None:
    jobs_path = Path(
        os.getenv("HERMES_LIFE_CRON_JOBS_PATH")
        or "/Users/pipi/.hermes/cron/jobs.json"
    )
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


if __name__ == "__main__":
    raise SystemExit(main())
