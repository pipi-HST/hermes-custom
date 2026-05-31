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

CREATE_LIFE_INTERACTION_CONTEXTS_TABLE = """
CREATE TABLE IF NOT EXISTS life_interaction_contexts (
    id TEXT PRIMARY KEY,
    context_type TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    expected_reply_kind TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    resolved_at TEXT
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
    headline TEXT NOT NULL DEFAULT '',
    report_text TEXT NOT NULL DEFAULT '',
    labels_json TEXT NOT NULL DEFAULT '{}',
    patterns_json TEXT NOT NULL DEFAULT '{}',
    recommendations_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    delivery_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(week_start, week_end)
)
"""

CREATE_LIFE_LONG_TERM_DECISIONS_TABLE = """
CREATE TABLE IF NOT EXISTS life_long_term_decisions (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    review_interval_days INTEGER NOT NULL DEFAULT 0,
    next_review_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT
)
"""

CREATE_LIFE_DECISION_REVIEWS_TABLE = """
CREATE TABLE IF NOT EXISTS life_decision_reviews (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    review_note TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    next_review_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    conn.execute(CREATE_LIFE_INTERACTION_CONTEXTS_TABLE)
    conn.execute(CREATE_LIFE_DAILY_REVIEWS_TABLE)
    conn.execute(CREATE_LIFE_WEEKLY_REVIEWS_TABLE)
    _add_column_if_missing(conn, "life_weekly_reviews", "headline", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "life_weekly_reviews", "report_text", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "life_weekly_reviews", "evidence_json", "TEXT NOT NULL DEFAULT '[]'")
    _add_column_if_missing(conn, "life_weekly_reviews", "delivery_json", "TEXT NOT NULL DEFAULT '{}'")
    conn.execute(CREATE_LIFE_LONG_TERM_DECISIONS_TABLE)
    conn.execute(CREATE_LIFE_DECISION_REVIEWS_TABLE)
    conn.commit()


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
