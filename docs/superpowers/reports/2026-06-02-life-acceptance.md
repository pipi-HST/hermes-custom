# Life Pipeline Acceptance Report

- status: passed
- generated_at: 2026-06-02T00:22:59.981744+08:00
- db_path: /Users/pipi/hermes-custom/data/captures.db

## Sections
- live_database: passed
- pipeline_coverage: passed
- runtime_audit: passed
- api: passed
- dashboard: passed

## Checks
- passed `life_audit`: life_audit status=ok
- passed `sleep_records_exist`: sleep_end=5
- passed `meal_records_exist`: meal=5
- passed `water_records_exist`: water=5
- passed `exercise_records_exist`: exercise=2
- passed `sedentary_records_exist`: sedentary_start=36
- passed `reminder_logs_exist`: reminder_types=8
- passed `daily_reviews_exist`: daily_reviews=3
- passed `weekly_reviews_exist`: weekly_reviews=1
- passed `pipeline_recording`: True
- passed `pipeline_sedentary`: True
- passed `pipeline_reminders`: True
- passed `pipeline_daily_review`: True
- passed `pipeline_weekly_review`: True
- passed `pipeline_runtime_clean`: True
- passed `api_runtime`: http=200, bytes=2048
- passed `api_summary`: http=200, bytes=1543
- passed `api_daily_status`: http=200, bytes=815
- passed `api_week`: http=200, bytes=2048
- passed `api_reviews`: http=200, bytes=699
- passed `life_dashboard_reachable`: http=200, bytes=2048
