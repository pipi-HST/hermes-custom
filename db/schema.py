"""SQLite schema for captures database.

Text content (title, summary, tags, etc.) is now stored in Markdown
frontmatter.  SQLite only keeps routing fields and review state.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

CREATE_CAPTURES_TABLE = """
CREATE TABLE IF NOT EXISTS captures (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL DEFAULT '',
    raw_path TEXT NOT NULL DEFAULT '',
    embedding BLOB,
    review_state TEXT NOT NULL DEFAULT 'new',
    review_interval INTEGER NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    last_reviewed TEXT,
    next_review TEXT,
    theme_scanned_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CAPTURE_COLUMNS = [
    "id",
    "file_path",
    "raw_path",
    "embedding",
    "review_state",
    "review_interval",
    "review_count",
    "last_reviewed",
    "next_review",
    "theme_scanned_at",
    "created_at",
    "updated_at",
]

CREATE_GARDEN_CARDS_TABLE = """
CREATE TABLE IF NOT EXISTS garden_cards (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    one_liner TEXT NOT NULL DEFAULT '',
    core_arguments TEXT NOT NULL DEFAULT '[]',
    knowledge_links TEXT NOT NULL DEFAULT '[]',
    key_materials TEXT NOT NULL DEFAULT '[]',
    my_view TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'seed',
    capture_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_reviewed_at TEXT,
    review_interval TEXT NOT NULL DEFAULT '1d',
    skip_count INTEGER DEFAULT 0
)
"""

CREATE_PENDING_QUESTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS pending_questions (
    id TEXT PRIMARY KEY,
    agent_type TEXT NOT NULL,
    question_text TEXT NOT NULL,
    context_ref TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    related_capture_ids TEXT NOT NULL DEFAULT '[]',
    matched_response TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
)
"""

CREATE_CAPTURE_DRAFTS_TABLE = """
CREATE TABLE IF NOT EXISTS capture_drafts (
    id TEXT PRIMARY KEY,
    raw_text TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    word_count INTEGER NOT NULL DEFAULT 0,
    draft_text TEXT NOT NULL DEFAULT '',
    instruction TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'raw_extracted',
    committed_capture_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_CHANGE_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT DEFAULT '',
    new_value TEXT DEFAULT '',
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def init_db(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(CREATE_CAPTURES_TABLE)
    _migrate_captures_table(conn)
    conn.execute(CREATE_CHANGE_LOG_TABLE)
    conn.execute(CREATE_GARDEN_CARDS_TABLE)
    conn.execute(CREATE_PENDING_QUESTIONS_TABLE)
    conn.execute(CREATE_CAPTURE_DRAFTS_TABLE)
    conn.commit()
    return conn


def _migrate_captures_table(conn: sqlite3.Connection) -> None:
    """Normalize old generated schemas to the slim captures table.

    Earlier versions created FTS triggers that still reference removed columns
    such as summary/tags/title. Leaving them in place makes inserts fail even
    when the visible captures columns look mostly correct.
    """
    for trigger in ("captures_ai", "captures_au", "captures_ad"):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    conn.execute("DROP TABLE IF EXISTS captures_fts")

    existing = [
        row[1] for row in conn.execute("PRAGMA table_info(captures)").fetchall()
    ]
    if existing == CAPTURE_COLUMNS:
        return

    conn.execute("DROP TABLE IF EXISTS captures_new")
    conn.execute(
        CREATE_CAPTURES_TABLE.replace(
            "CREATE TABLE IF NOT EXISTS captures",
            "CREATE TABLE captures_new",
            1,
        )
    )

    common = [column for column in CAPTURE_COLUMNS if column in existing]
    if common:
        columns = ", ".join(common)
        conn.execute(
            f"INSERT OR REPLACE INTO captures_new ({columns}) "
            f"SELECT {columns} FROM captures"
        )

    conn.execute("DROP TABLE captures")
    conn.execute("ALTER TABLE captures_new RENAME TO captures")
