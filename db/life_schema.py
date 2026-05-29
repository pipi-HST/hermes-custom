"""SQLite schema for Life Assistant tables."""

from __future__ import annotations

import sqlite3

from db.schema import init_db

CREATE_LIFE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS life_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    ended_at TEXT,
    duration_minutes INTEGER,
    source TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'confirmed',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_LIFE_REMINDER_RULES_TABLE = """
CREATE TABLE IF NOT EXISTS life_reminder_rules (
    id TEXT PRIMARY KEY,
    rule_type TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    schedule_json TEXT NOT NULL DEFAULT '{}',
    threshold_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_LIFE_REMINDER_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS life_reminder_logs (
    id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL DEFAULT '',
    reminder_type TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    sent_at TEXT,
    status TEXT NOT NULL,
    user_response TEXT NOT NULL DEFAULT '',
    resolved_at TEXT,
    dedupe_key TEXT NOT NULL UNIQUE,
    error TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_LIFE_LOCKS_TABLE = """
CREATE TABLE IF NOT EXISTS life_locks (
    name TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_LIFE_DAILY_REVIEWS_TABLE = """
CREATE TABLE IF NOT EXISTS life_daily_reviews (
    id TEXT PRIMARY KEY,
    review_date TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL DEFAULT '',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    notes_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_LIFE_WEEKLY_REVIEWS_TABLE = """
CREATE TABLE IF NOT EXISTS life_weekly_reviews (
    id TEXT PRIMARY KEY,
    week_start TEXT NOT NULL,
    week_end TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    labels_json TEXT NOT NULL DEFAULT '{}',
    patterns_json TEXT NOT NULL DEFAULT '{}',
    recommendations_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(week_start, week_end)
)
"""


def init_life_db(db_path: str) -> sqlite3.Connection:
    """Initialize the shared Hermes DB with Life Assistant tables."""
    conn = init_db(db_path)
    migrate_life_schema(conn)
    return conn


def migrate_life_schema(conn: sqlite3.Connection) -> None:
    """Create additive Life Assistant tables on an existing connection."""
    conn.execute(CREATE_LIFE_EVENTS_TABLE)
    conn.execute(CREATE_LIFE_REMINDER_RULES_TABLE)
    conn.execute(CREATE_LIFE_REMINDER_LOGS_TABLE)
    conn.execute(CREATE_LIFE_LOCKS_TABLE)
    conn.execute(CREATE_LIFE_DAILY_REVIEWS_TABLE)
    conn.execute(CREATE_LIFE_WEEKLY_REVIEWS_TABLE)
    conn.commit()
