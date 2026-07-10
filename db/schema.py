"""SQLite schema for captures database.

Text content (title, summary, tags, etc.) is now stored in Markdown
frontmatter.  SQLite only keeps routing fields and review state.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from db.concurrency import SerializedConnection

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
    card_kind TEXT NOT NULL DEFAULT 'viewpoint',
    schema_version TEXT NOT NULL DEFAULT 'garden_card.v1',
    payload_json TEXT NOT NULL DEFAULT '{}',
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

CREATE_GARDEN_DRAFTS_TABLE = """
CREATE TABLE IF NOT EXISTS garden_drafts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    one_liner TEXT NOT NULL DEFAULT '',
    card_kind TEXT NOT NULL DEFAULT 'viewpoint',
    schema_version TEXT NOT NULL DEFAULT 'garden_card.v1',
    payload_json TEXT NOT NULL DEFAULT '{}',
    core_arguments TEXT NOT NULL DEFAULT '[]',
    knowledge_links TEXT NOT NULL DEFAULT '[]',
    key_materials TEXT NOT NULL DEFAULT '[]',
    markdown TEXT NOT NULL DEFAULT '',
    capture_ids TEXT NOT NULL DEFAULT '[]',
    question_id TEXT NOT NULL DEFAULT '',
    instruction TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft_ready',
    committed_card_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    capture_intent TEXT NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'raw_extracted',
    committed_capture_id TEXT NOT NULL DEFAULT '',
    pending_capture_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_CAPTURE_EMBEDDINGS_TABLE = """
CREATE TABLE IF NOT EXISTS capture_embeddings (
    id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('identity', 'section')),
    section_id TEXT NOT NULL DEFAULT '',
    section_title TEXT NOT NULL DEFAULT '',
    index_text TEXT NOT NULL DEFAULT '',
    embedding BLOB,
    source_hash TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(capture_id, kind, section_id)
)
"""

CREATE_CAPTURE_EMBEDDINGS_SEARCH_INDEX = """
CREATE INDEX IF NOT EXISTS idx_capture_embeddings_search
ON capture_embeddings (kind)
WHERE embedding IS NOT NULL
"""

CREATE_CAPTURE_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS capture_metadata (
    capture_id TEXT PRIMARY KEY,
    outline_json TEXT NOT NULL DEFAULT '[]',
    outline_source_hash TEXT NOT NULL DEFAULT '',
    directory_rendered_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_REVIEW_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS review_items (
    id TEXT PRIMARY KEY,
    object_type TEXT NOT NULL DEFAULT 'capture',
    object_id TEXT NOT NULL DEFAULT '',
    capture_id TEXT NOT NULL DEFAULT '',
    section_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL DEFAULT '',
    answer_hint TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL DEFAULT '',
    importance INTEGER NOT NULL DEFAULT 1,
    review_state TEXT NOT NULL DEFAULT 'new',
    review_interval INTEGER NOT NULL DEFAULT 1,
    review_count INTEGER NOT NULL DEFAULT 0,
    view_count INTEGER NOT NULL DEFAULT 0,
    last_reviewed TEXT,
    last_viewed TEXT,
    next_review TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(object_type, object_id, section_id)
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

CREATE_AGENT_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    intent TEXT NOT NULL,
    trigger TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    errors_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
)
"""

CREATE_AGENT_RUN_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS agent_run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_AGENT_RUN_OBJECTS_TABLE = """
CREATE TABLE IF NOT EXISTS agent_run_objects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'subject',
    source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(run_id, object_type, object_id, role)
)
"""

CREATE_AGENT_RUN_OBJECTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_agent_run_objects_lookup
ON agent_run_objects (object_type, object_id, run_id)
"""

CREATE_READING_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS reading_sessions (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    source_title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    focus TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    committed_artifact_id TEXT NOT NULL DEFAULT '',
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_turn_at TEXT
)
"""

CREATE_READING_TURNS_TABLE = """
CREATE TABLE IF NOT EXISTS reading_turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL DEFAULT 1,
    user_message TEXT NOT NULL DEFAULT '',
    assistant_answer TEXT NOT NULL DEFAULT '',
    focus TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'medium',
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_READING_EVIDENCE_TABLE = """
CREATE TABLE IF NOT EXISTS reading_evidence (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    excerpt TEXT NOT NULL DEFAULT '',
    used_for TEXT NOT NULL DEFAULT '',
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_READING_DRAFTS_TABLE = """
CREATE TABLE IF NOT EXISTS reading_drafts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    output_type TEXT NOT NULL DEFAULT '',
    target_capture_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    draft_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_CAPTURE_VERSION_BACKUPS_TABLE = """
CREATE TABLE IF NOT EXISTS capture_version_backups (
    id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL DEFAULT '',
    reading_draft_id TEXT NOT NULL DEFAULT '',
    backup_file_path TEXT NOT NULL DEFAULT '',
    previous_file_hash TEXT NOT NULL DEFAULT '',
    new_file_hash TEXT NOT NULL DEFAULT '',
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_KNOWLEDGE_WRITE_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS knowledge_write_jobs (
    id TEXT PRIMARY KEY,
    intent TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    step TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    db_table TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_KNOWLEDGE_EDGES_TABLE = """
CREATE TABLE IF NOT EXISTS knowledge_edges (
    id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 1.0,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    generated_by TEXT NOT NULL DEFAULT 'auto',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_kind, source_id, target_kind, target_id, relation_type, generated_by)
)
"""

CREATE_KNOWLEDGE_EDGES_LOOKUP_INDEX = """
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_lookup
ON knowledge_edges (source_kind, source_id, target_kind, target_id)
"""

CREATE_KNOWLEDGE_EDGES_RELATION_INDEX = """
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_relation
ON knowledge_edges (relation_type, score)
"""


def init_db(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        check_same_thread=False,
        factory=SerializedConnection,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(CREATE_CAPTURES_TABLE)
    _migrate_captures_table(conn)
    conn.execute(CREATE_CHANGE_LOG_TABLE)
    conn.execute(CREATE_AGENT_RUNS_TABLE)
    conn.execute(CREATE_AGENT_RUN_EVENTS_TABLE)
    conn.execute(CREATE_AGENT_RUN_OBJECTS_TABLE)
    conn.execute(CREATE_AGENT_RUN_OBJECTS_INDEX)
    conn.execute(CREATE_GARDEN_CARDS_TABLE)
    conn.execute(CREATE_GARDEN_DRAFTS_TABLE)
    _migrate_garden_tables(conn)
    conn.execute(CREATE_PENDING_QUESTIONS_TABLE)
    conn.execute(CREATE_CAPTURE_DRAFTS_TABLE)
    _migrate_capture_drafts_table(conn)
    conn.execute(CREATE_CAPTURE_EMBEDDINGS_TABLE)
    conn.execute(CREATE_CAPTURE_EMBEDDINGS_SEARCH_INDEX)
    conn.execute(CREATE_CAPTURE_METADATA_TABLE)
    conn.execute(CREATE_REVIEW_ITEMS_TABLE)
    _migrate_review_items_table(conn)
    conn.execute(CREATE_READING_SESSIONS_TABLE)
    conn.execute(CREATE_READING_TURNS_TABLE)
    conn.execute(CREATE_READING_EVIDENCE_TABLE)
    conn.execute(CREATE_READING_DRAFTS_TABLE)
    conn.execute(CREATE_CAPTURE_VERSION_BACKUPS_TABLE)
    conn.execute(CREATE_KNOWLEDGE_WRITE_JOBS_TABLE)
    conn.execute(CREATE_KNOWLEDGE_EDGES_TABLE)
    conn.execute(CREATE_KNOWLEDGE_EDGES_LOOKUP_INDEX)
    conn.execute(CREATE_KNOWLEDGE_EDGES_RELATION_INDEX)
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


def _migrate_capture_drafts_table(conn: sqlite3.Connection) -> None:
    existing = [
        row[1] for row in conn.execute("PRAGMA table_info(capture_drafts)").fetchall()
    ]
    if "capture_intent" not in existing:
        conn.execute(
            "ALTER TABLE capture_drafts "
            "ADD COLUMN capture_intent TEXT NOT NULL DEFAULT '{}'"
        )
    if "pending_capture_id" not in existing:
        conn.execute(
            "ALTER TABLE capture_drafts "
            "ADD COLUMN pending_capture_id TEXT NOT NULL DEFAULT ''"
        )


def _migrate_garden_tables(conn: sqlite3.Connection) -> None:
    for table in ("garden_cards", "garden_drafts"):
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "card_kind" not in existing:
            conn.execute(
                f"ALTER TABLE {table} "
                "ADD COLUMN card_kind TEXT NOT NULL DEFAULT 'viewpoint'"
            )
        if "schema_version" not in existing:
            conn.execute(
                f"ALTER TABLE {table} "
                "ADD COLUMN schema_version TEXT NOT NULL DEFAULT 'garden_card.v1'"
            )
        if "payload_json" not in existing:
            conn.execute(
                f"ALTER TABLE {table} "
                "ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'"
            )


def _migrate_review_items_table(conn: sqlite3.Connection) -> None:
    existing = {
        row[1] for row in conn.execute("PRAGMA table_info(review_items)").fetchall()
    }
    if "view_count" not in existing:
        conn.execute(
            "ALTER TABLE review_items "
            "ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0"
        )
    if "last_viewed" not in existing:
        conn.execute("ALTER TABLE review_items ADD COLUMN last_viewed TEXT")
