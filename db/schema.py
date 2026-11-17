"""SQLite schema for captures database.

Text content (title, summary, tags, etc.) is now stored in Markdown
frontmatter.  SQLite only keeps routing fields and review state.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from db.concurrency import SerializedConnection, transaction

CREATE_CAPTURES_TABLE = """
CREATE TABLE IF NOT EXISTS captures (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL DEFAULT '',
    raw_path TEXT NOT NULL DEFAULT '',
    archived_at TEXT,
    theme_scanned_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_APP_META_TABLE = """
CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
)
"""

CREATE_CAPTURES_CREATED_AT_INDEX = """
CREATE INDEX IF NOT EXISTS idx_captures_created_at
ON captures(created_at DESC)
"""

CREATE_CAPTURES_UPDATED_AT_INDEX = """
CREATE INDEX IF NOT EXISTS idx_captures_updated_at
ON captures(updated_at DESC)
"""

CREATE_CAPTURE_DRAFTS_UPDATED_AT_INDEX = """
CREATE INDEX IF NOT EXISTS idx_capture_drafts_updated_at
ON capture_drafts(updated_at DESC)
"""

CREATE_CAPTURE_DRAFTS_STATUS_UPDATED_INDEX = """
CREATE INDEX IF NOT EXISTS idx_capture_drafts_status_updated
ON capture_drafts(status, updated_at DESC)
"""

CREATE_CAPTURE_DRAFTS_FOLDER_INDEX = """
CREATE INDEX IF NOT EXISTS idx_capture_drafts_folder
ON capture_drafts(
    TRIM(COALESCE(NULLIF(json_extract(capture_intent, '$.folder_path'), ''), 'Inbox'))
)
"""

CREATE_GARDEN_CARDS_RECALL_INDEX = """
CREATE INDEX IF NOT EXISTS idx_garden_cards_recall
ON garden_cards(citation_count DESC, updated_at DESC)
"""

CAPTURE_COLUMNS = [
    "id",
    "file_path",
    "raw_path",
    "archived_at",
    "theme_scanned_at",
    "created_at",
    "updated_at",
]

# ADD COLUMN cannot use a non-constant default such as datetime('now'), so
# missing timestamp columns are added nullable and backfilled once.
CAPTURE_COLUMN_ALTER_DDL = {
    "file_path": "TEXT NOT NULL DEFAULT ''",
    "raw_path": "TEXT NOT NULL DEFAULT ''",
    "archived_at": "TEXT",
    "theme_scanned_at": "TEXT",
    "created_at": "TEXT",
    "updated_at": "TEXT",
}

SCHEMA_VERSION = 4

CREATE_GARDEN_CARDS_TABLE = """
CREATE TABLE IF NOT EXISTS garden_cards (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    content_markdown TEXT NOT NULL DEFAULT '',
    card_kind TEXT NOT NULL DEFAULT 'viewpoint',
    schema_version TEXT NOT NULL DEFAULT 'garden_card.v2',
    payload_json TEXT NOT NULL DEFAULT '{}',
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
    card_kind TEXT NOT NULL DEFAULT 'viewpoint',
    schema_version TEXT NOT NULL DEFAULT 'garden_card.v2',
    payload_json TEXT NOT NULL DEFAULT '{}',
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

CREATE_CONFIRMATION_TOKENS_TABLE = """
CREATE TABLE IF NOT EXISTS confirmation_tokens (
    token_hash TEXT PRIMARY KEY,
    intent TEXT NOT NULL,
    object_id TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    issued_to TEXT NOT NULL DEFAULT 'api'
)
"""

CREATE_CONFIRMATION_TOKENS_OBJECT_INDEX = """
CREATE INDEX IF NOT EXISTS idx_confirmation_tokens_object
ON confirmation_tokens(intent, object_id)
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

# CJK-aware keyword index.  Content is pre-tokenised by ``db.fts_tokenizer``
# (overlapping bigrams) because ``unicode61`` alone cannot match Chinese
# substrings and ``trigram`` cannot match two-character words.
CREATE_CAPTURE_SEARCH_FTS_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS capture_search_fts USING fts5(
    capture_id UNINDEXED,
    kind UNINDEXED,
    section_id UNINDEXED,
    section_title_tokens,
    index_text_tokens,
    tokenize='unicode61'
)
"""

CREATE_CAPTURE_SEARCH_FTS_MAP_TABLE = """
CREATE TABLE IF NOT EXISTS capture_search_fts_map (
    capture_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    section_id TEXT NOT NULL,
    fts_rowid INTEGER NOT NULL,
    source_hash TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (capture_id, kind, section_id)
)
"""

CREATE_CAPTURE_SEARCH_FTS_MAP_ROWID_INDEX = """
CREATE INDEX IF NOT EXISTS idx_capture_search_fts_map_rowid
ON capture_search_fts_map (fts_rowid)
"""

CREATE_CITATION_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS citation_events (
    id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL DEFAULT '',
    cited_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

CREATE_CITATION_EVENTS_CARD_INDEX = """
CREATE INDEX IF NOT EXISTS idx_citation_events_card ON citation_events(card_id)
"""


def _dict_row(cursor: sqlite3.Cursor, row: tuple) -> dict:
    """Materialise rows as plain dicts at fetch time.

    Kept for callers that opt in via ``db.concurrency.fetch_dict``; the shared
    connection itself keeps ``sqlite3.Row`` because a lot of code still reads
    columns positionally.
    """
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def init_db(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        check_same_thread=False,
        isolation_level=None,  # autocommit; explicit transactions live in db.concurrency
        factory=SerializedConnection,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # Once the schema version is current, only the timestamp self-heal runs;
    # re-executing the whole DDL batch on every bridge/tool call was pure
    # overhead.  Keep the timestamp pass because SQLite's datetime('now')
    # default still writes the legacy space-separated form.
    if (
        conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION
        and _table_exists(conn, "captures")
    ):
        # An interrupted captures-table swap can leave captures_new behind
        # even after the schema version was stamped.
        _recover_interrupted_captures_swap(conn)
        _backfill_garden_content(conn)
        _migrate_garden_v1_columns(conn)
        _drop_capture_review_columns(conn)
        for ddl in (
            CREATE_CAPTURES_CREATED_AT_INDEX,
            CREATE_CAPTURES_UPDATED_AT_INDEX,
        ):
            conn.execute(ddl)
        if _table_exists(conn, "capture_drafts"):
            _create_capture_draft_indexes(conn)
        if capture_search_fts_available(conn):
            try:
                fts_count = conn.execute(
                    "SELECT count(*) FROM capture_search_fts"
                ).fetchone()[0]
                map_count = conn.execute(
                    "SELECT count(*) FROM capture_search_fts_map"
                ).fetchone()[0]
                if fts_count == 0 or map_count != fts_count:
                    _backfill_capture_search_fts(conn)
                    _repair_capture_search_fts_map(conn)
            except sqlite3.OperationalError:
                pass
        _ensure_timestamp_normalization(conn)
        return conn
    # The whole DDL batch runs in one explicit transaction so an interrupted
    # upgrade cannot leave the database half-migrated (e.g. rows parked in
    # ``captures_new`` after ``captures`` was already dropped).  PRAGMAs above
    # stay outside: journal mode cannot be changed inside a transaction.
    with transaction(conn):
        conn.execute(CREATE_APP_META_TABLE)
        conn.execute(CREATE_CAPTURES_TABLE)
        _migrate_captures_table(conn)
        conn.execute(CREATE_CAPTURES_CREATED_AT_INDEX)
        conn.execute(CREATE_CAPTURES_UPDATED_AT_INDEX)
        conn.execute(CREATE_CHANGE_LOG_TABLE)
        conn.execute(CREATE_AGENT_RUNS_TABLE)
        conn.execute(CREATE_AGENT_RUN_EVENTS_TABLE)
        conn.execute(CREATE_AGENT_RUN_OBJECTS_TABLE)
        conn.execute(CREATE_AGENT_RUN_OBJECTS_INDEX)
        conn.execute(CREATE_GARDEN_CARDS_TABLE)
        conn.execute(CREATE_GARDEN_DRAFTS_TABLE)
        _migrate_garden_tables(conn)
        _normalize_garden_schema_version(conn)
        _migrate_garden_v1_columns(conn)
        conn.execute(CREATE_GARDEN_CARDS_RECALL_INDEX)
        conn.execute(CREATE_PENDING_QUESTIONS_TABLE)
        conn.execute(CREATE_CAPTURE_DRAFTS_TABLE)
        _migrate_capture_drafts_table(conn)
        _create_capture_draft_indexes(conn)
        conn.execute(CREATE_CAPTURE_EMBEDDINGS_TABLE)
        conn.execute(CREATE_CAPTURE_EMBEDDINGS_SEARCH_INDEX)
        _migrate_legacy_embeddings(conn)
        _drop_legacy_embedding_column(conn)
        _drop_capture_review_columns(conn)
        conn.execute(CREATE_CAPTURE_METADATA_TABLE)
        _migrate_capture_metadata_table(conn)
        _create_capture_search_fts(conn)
        conn.execute(CREATE_CAPTURE_SEARCH_FTS_MAP_TABLE)
        conn.execute(CREATE_CAPTURE_SEARCH_FTS_MAP_ROWID_INDEX)
        _backfill_capture_search_fts(conn)
        _repair_capture_search_fts_map(conn)
        conn.execute(CREATE_CITATION_EVENTS_TABLE)
        conn.execute(CREATE_CITATION_EVENTS_CARD_INDEX)
        conn.execute(CREATE_REVIEW_ITEMS_TABLE)
        _migrate_review_items_table(conn)
        conn.execute(CREATE_READING_SESSIONS_TABLE)
        conn.execute(CREATE_READING_TURNS_TABLE)
        conn.execute(CREATE_READING_EVIDENCE_TABLE)
        conn.execute(CREATE_READING_DRAFTS_TABLE)
        conn.execute(CREATE_CAPTURE_VERSION_BACKUPS_TABLE)
        conn.execute(CREATE_KNOWLEDGE_WRITE_JOBS_TABLE)
        conn.execute(CREATE_CONFIRMATION_TOKENS_TABLE)
        conn.execute(CREATE_CONFIRMATION_TOKENS_OBJECT_INDEX)
        conn.execute(CREATE_KNOWLEDGE_EDGES_TABLE)
        conn.execute(CREATE_KNOWLEDGE_EDGES_LOOKUP_INDEX)
        conn.execute(CREATE_KNOWLEDGE_EDGES_RELATION_INDEX)
        _ensure_timestamp_normalization(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn



_TIMESTAMP_COLUMNS = frozenset(
    {
        "created_at",
        "updated_at",
        "last_reviewed",
        "next_review",
        "theme_scanned_at",
        "issued_at",
        "expires_at",
        "used_at",
        "last_viewed",
        "directory_rendered_at",
    }
)


TIMESTAMP_FORMAT_MARKER = "timestamp_format_v1"


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _timestamp_pairs(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    tables = [
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    pairs: list[tuple[str, str]] = []
    for table in tables:
        columns = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()
        }
        for column in sorted(columns & _TIMESTAMP_COLUMNS):
            pairs.append((table, column))
    return pairs


def _migrate_timestamp_formats(conn: sqlite3.Connection) -> None:
    """Normalise existing SQLite-default timestamps to ISO-8601 once.

    After this pass, ``_install_timestamp_triggers`` keeps new writes normalised
    so ``init_db`` does not need to rescan the whole database on every open.
    Values that match the legacy shape but are not real timestamps keep their
    original text: ``strftime`` returns NULL for those.
    """
    for table, column in _timestamp_pairs(conn):
        conn.execute(
            f"UPDATE {_quote_ident(table)} "
            f"SET {_quote_ident(column)} = "
            f"COALESCE(strftime(?, {_quote_ident(column)}), {_quote_ident(column)}) "
            f"WHERE {_quote_ident(column)} GLOB ?",
            ("%Y-%m-%dT%H:%M:%SZ", "????-??-?? ??:??:??*"),
        )


def _install_timestamp_triggers(conn: sqlite3.Connection) -> None:
    """Normalise timestamp columns at write time instead of rescanning on boot."""
    for table, column in _timestamp_pairs(conn):
        table_sql = _quote_ident(table)
        column_sql = _quote_ident(column)
        trigger_prefix = f"trg_norm_{table}_{column}"
        for suffix, event, when in (
            ("insert", "INSERT", f"NEW.{column_sql}"),
            ("update", f"UPDATE OF {column_sql}", f"NEW.{column_sql}"),
        ):
            trigger_name = _quote_ident(f"{trigger_prefix}_{suffix}")
            try:
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {trigger_name} "
                    f"AFTER {event} ON {table_sql} "
                    f"WHEN {when} GLOB '????-??-?? ??:??:??*' "
                    f"BEGIN "
                    f"UPDATE {table_sql} SET {column_sql} = "
                    f"COALESCE(strftime('%Y-%m-%dT%H:%M:%SZ', {when}), {when}) "
                    f"WHERE rowid = NEW.rowid; "
                    f"END"
                )
            except sqlite3.OperationalError:
                continue


def _ensure_timestamp_normalization(conn: sqlite3.Connection) -> None:
    """Run the one-time migration, then install write-time triggers."""
    conn.execute(CREATE_APP_META_TABLE)
    marker = conn.execute(
        "SELECT value FROM app_meta WHERE key = ?", (TIMESTAMP_FORMAT_MARKER,)
    ).fetchone()
    if marker is None or str(marker["value"]) != "1":
        _migrate_timestamp_formats(conn)
    _install_timestamp_triggers(conn)
    conn.execute(
        "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, '1')",
        (TIMESTAMP_FORMAT_MARKER,),
    )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _recover_interrupted_captures_swap(conn: sqlite3.Connection) -> None:
    """Promote ``captures_new`` when a captures rebuild was interrupted.

    :func:`_migrate_captures_table` copies rows into ``captures_new``, drops
    ``captures`` and renames the copy back.  A crash between those steps used to
    leave the data parked in ``captures_new`` while the next startup recreated
    an empty ``captures`` table, silently losing every capture.  If the swap is
    incomplete and ``captures_new`` actually holds rows, finish it here.
    """
    if not _table_exists(conn, "captures_new"):
        return
    if _table_exists(conn, "captures") and conn.execute(
        "SELECT 1 FROM captures LIMIT 1"
    ).fetchone() is not None:
        return
    if conn.execute("SELECT 1 FROM captures_new LIMIT 1").fetchone() is None:
        return
    conn.execute("DROP TABLE IF EXISTS captures")
    conn.execute("ALTER TABLE captures_new RENAME TO captures")


def _create_capture_draft_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_CAPTURE_DRAFTS_UPDATED_AT_INDEX)
    conn.execute(CREATE_CAPTURE_DRAFTS_STATUS_UPDATED_INDEX)
    try:
        conn.execute(CREATE_CAPTURE_DRAFTS_FOLDER_INDEX)
    except sqlite3.OperationalError:
        # Older SQLite builds without JSON1 keep the Python fallback path.
        pass


def _migrate_captures_table(conn: sqlite3.Connection) -> None:
    """Normalize old generated schemas to the slim captures table.

    Earlier versions created FTS triggers that still reference removed columns
    such as summary/tags/title. Leaving them in place makes inserts fail even
    when the visible captures columns look mostly correct.
    """
    for trigger in ("captures_ai", "captures_au", "captures_ad"):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    conn.execute("DROP TABLE IF EXISTS captures_fts")

    _recover_interrupted_captures_swap(conn)

    existing = [
        row["name"] for row in conn.execute("PRAGMA table_info(captures)").fetchall()
    ]
    if not existing:
        return
    if "id" not in existing:
        # A table without the primary key cannot be repaired with ALTER; the
        # legacy rebuild is the only safe path.
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
        return

    # Preserve unknown/legacy columns instead of rebuilding the table and
    # copying only the current whitelist (which silently dropped user data).
    added_timestamps: list[str] = []
    for column in CAPTURE_COLUMNS:
        if column in existing:
            continue
        ddl = CAPTURE_COLUMN_ALTER_DDL[column]
        conn.execute(f"ALTER TABLE captures ADD COLUMN {column} {ddl}")
        if column in {"created_at", "updated_at"}:
            added_timestamps.append(column)
    for column in added_timestamps:
        conn.execute(
            f"UPDATE captures SET {column} = COALESCE({column}, datetime('now'))"
        )


def _migrate_capture_drafts_table(conn: sqlite3.Connection) -> None:
    existing = [
        row["name"] for row in conn.execute("PRAGMA table_info(capture_drafts)").fetchall()
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
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "card_kind" not in existing:
            conn.execute(
                f"ALTER TABLE {table} "
                "ADD COLUMN card_kind TEXT NOT NULL DEFAULT 'viewpoint'"
            )
        if "schema_version" not in existing:
            conn.execute(
                f"ALTER TABLE {table} "
                "ADD COLUMN schema_version TEXT NOT NULL DEFAULT 'garden_card.v2'"
            )
        if "payload_json" not in existing:
            conn.execute(
                f"ALTER TABLE {table} "
                "ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'"
            )

    # Card-only lineage columns (V3).  ``source_theme`` closes a real contract
    # gap: bridge/rag_bridge.py has always read ``card["source_theme"]`` but the
    # column never existed, so the read silently produced ''.
    card_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(garden_cards)").fetchall()
    }
    added: list[str] = []
    for column, ddl in (
        ("source_theme", "TEXT NOT NULL DEFAULT 'Inbox'"),
        ("embedding", "BLOB"),
        ("citation_count", "INTEGER NOT NULL DEFAULT 0"),
        ("last_cited_at", "TEXT"),
        ("embedding_source_hash", "TEXT NOT NULL DEFAULT ''"),
        ("embedding_updated_at", "TEXT"),
        ("content_markdown", "TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in card_columns:
            conn.execute(f"ALTER TABLE garden_cards ADD COLUMN {column} {ddl}")
            added.append(column)
    if "source_theme" in added:
        # One-time backfill for cards that already carried a theme in payload.
        try:
            conn.execute(
                "UPDATE garden_cards "
                "SET source_theme = json_extract(payload_json, '$.source_theme') "
                "WHERE COALESCE(json_extract(payload_json, '$.source_theme'), '') != ''"
            )
        except sqlite3.OperationalError:
            pass
    _backfill_garden_content(conn)


def _migrate_legacy_embeddings(conn: sqlite3.Connection) -> None:
    """Move legacy ``captures.embedding`` rows into the identity index once.

    ``captures.embedding`` was the old whole-note vector.  The live index is
    ``capture_embeddings``; keeping both readable makes RAG fall back to stale
    vectors after a re-index.  Backfill then clear the legacy column.
    """
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(captures)").fetchall()
    }
    if "embedding" not in columns:
        return
    rows = conn.execute(
        "SELECT id, file_path, embedding FROM captures WHERE embedding IS NOT NULL"
    ).fetchall()
    for row in rows:
        capture_id = str(row["id"])
        conn.execute(
            """INSERT OR IGNORE INTO capture_embeddings
                   (id, capture_id, kind, section_id, section_title,
                    index_text, embedding, source_hash, file_path)
               VALUES (?, ?, 'identity', '', '', '', ?, 'legacy-captures-embedding', ?)""",
            (
                f"legacy-{capture_id}",
                capture_id,
                row["embedding"],
                str(row["file_path"] or ""),
            ),
        )
    if rows:
        conn.execute("UPDATE captures SET embedding = NULL")


def _drop_legacy_embedding_column(conn: sqlite3.Connection) -> None:
    """Drop ``captures.embedding`` once the identity index owns the vectors.

    SQLite < 3.35 cannot drop a column; those installs keep an unused column
    (all values are NULL after the backfill) instead of failing to open.
    """
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(captures)").fetchall()
    }
    if "embedding" not in columns:
        return
    try:
        conn.execute("ALTER TABLE captures DROP COLUMN embedding")
    except sqlite3.OperationalError:
        pass


CAPTURE_REVIEW_COLUMNS = (
    "review_state",
    "review_interval",
    "review_count",
    "last_reviewed",
    "next_review",
)


def _drop_capture_review_columns(conn: sqlite3.Connection) -> None:
    """Move the archived flag to ``archived_at`` and drop capture review state.

    Review scheduling lives in ``review_items``.  ``review_state`` was also the
    only archive marker, so preserve it before dropping the scheduler columns.
    """
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(captures)").fetchall()
    }
    if "archived_at" not in columns:
        conn.execute("ALTER TABLE captures ADD COLUMN archived_at TEXT")
    if "review_state" in columns:
        conn.execute(
            "UPDATE captures SET archived_at = COALESCE(archived_at, datetime('now')) "
            "WHERE COALESCE(review_state, '') = 'archived'"
        )
    for column in CAPTURE_REVIEW_COLUMNS:
        for suffix in ("insert", "update"):
            conn.execute(
                f'DROP TRIGGER IF EXISTS "trg_norm_captures_{column}_{suffix}"'
            )
    for column in CAPTURE_REVIEW_COLUMNS:
        if column not in columns:
            continue
        try:
            conn.execute(f"ALTER TABLE captures DROP COLUMN {column}")
        except sqlite3.OperationalError:
            pass


def _normalize_garden_schema_version(conn: sqlite3.Connection) -> None:
    """Normalize persisted garden cards and drafts to the v2 payload schema."""
    for table in ("garden_cards", "garden_drafts"):
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "schema_version" in columns:
            conn.execute(
                f"UPDATE {table} SET schema_version = 'garden_card.v2' "
                "WHERE schema_version = 'garden_card.v1'"
            )


def _backfill_garden_content(conn: sqlite3.Connection) -> None:
    """Backfill top-level card bodies from payloads, including new seed rows."""
    try:
        conn.execute(
            "UPDATE garden_cards "
            "SET content_markdown = json_extract(payload_json, '$.content_markdown') "
            "WHERE COALESCE(content_markdown, '') = '' "
            "  AND COALESCE(json_extract(payload_json, '$.content_markdown'), '') != ''"
        )
    except sqlite3.OperationalError:
        pass


GARDEN_V1_COLUMNS = (
    "one_liner",
    "core_arguments",
    "knowledge_links",
    "key_materials",
    "my_view",
)


def _migrate_garden_v1_columns(conn: sqlite3.Connection) -> None:
    """Fold the v1 payload mirrors into ``payload_json`` and drop them.

    ``one_liner``/``core_arguments``/... duplicated the v2 payload.  Read them
    one last time for rows that only ever had the v1 columns, then remove the
    duplicate columns.  SQLite < 3.35 keeps them (unused) instead of failing.
    """
    from agents.garden_card_payload import normalize_garden_payload, parse_payload_json

    for table in ("garden_cards", "garden_drafts"):
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        present = [column for column in GARDEN_V1_COLUMNS if column in columns]
        if not present:
            continue
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        for row in rows:
            legacy = dict(row)
            payload = parse_payload_json(legacy.get("payload_json"))
            normalized = normalize_garden_payload(payload, legacy=legacy)
            conn.execute(
                f"UPDATE {table} SET payload_json = ? WHERE id = ?",
                (
                    json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                    legacy["id"],
                ),
            )
        for column in present:
            try:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
            except sqlite3.OperationalError:
                pass


def _migrate_capture_metadata_table(conn: sqlite3.Connection) -> None:
    """Add the indexed folder column that mirrors frontmatter ``folder_path``."""
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(capture_metadata)").fetchall()
    }
    if "folder_path" not in existing:
        conn.execute(
            "ALTER TABLE capture_metadata "
            "ADD COLUMN folder_path TEXT NOT NULL DEFAULT 'Inbox'"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_capture_metadata_folder "
        "ON capture_metadata(folder_path)"
    )


def _create_capture_search_fts(conn: sqlite3.Connection) -> bool:
    """Create the CJK keyword index; tolerate builds without FTS5."""
    try:
        conn.execute(CREATE_CAPTURE_SEARCH_FTS_TABLE)
        return True
    except sqlite3.OperationalError:
        return False


def capture_search_fts_available(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='capture_search_fts'"
    ).fetchone()
    return row is not None


def _backfill_capture_search_fts(conn: sqlite3.Connection) -> int:
    """Populate a freshly created keyword index from existing chunk rows.

    A database upgraded from a pre-FTS build has embeddings but an empty FTS
    table.  Without this, keyword search returns nothing and looks exactly like
    a broken tokenizer, so the upgrade must self-heal instead of waiting for
    someone to run the maintenance script.
    """
    from db.fts_tokenizer import tokenize_for_fts

    if not capture_search_fts_available(conn):
        return 0
    try:
        existing = conn.execute("SELECT count(*) FROM capture_search_fts").fetchone()[0]
        if existing:
            return 0
        rows = conn.execute(
            "SELECT capture_id, kind, section_id, section_title, index_text "
            "FROM capture_embeddings"
        ).fetchall()
    except sqlite3.OperationalError:
        return 0
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO capture_search_fts "
        "(capture_id, kind, section_id, section_title_tokens, index_text_tokens) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (
                str(row["capture_id"] or ""),
                str(row["kind"] or ""),
                str(row["section_id"] or ""),
                tokenize_for_fts(str(row["section_title"] or "")),
                tokenize_for_fts(str(row["index_text"] or "")),
            )
            for row in rows
        ],
    )
    return len(rows)


def _repair_capture_search_fts_map(conn: sqlite3.Connection) -> None:
    """Backfill the rowid map for databases created before it existed."""
    try:
        fts_count = conn.execute("SELECT count(*) FROM capture_search_fts").fetchone()[0]
        map_count = conn.execute("SELECT count(*) FROM capture_search_fts_map").fetchone()[0]
        if fts_count == map_count:
            return
        conn.execute("DELETE FROM capture_search_fts_map")
        conn.execute(
            """INSERT INTO capture_search_fts_map
               (capture_id, kind, section_id, fts_rowid, source_hash)
               SELECT f.capture_id, f.kind, f.section_id, f.rowid,
                      COALESCE(ce.source_hash, '')
               FROM capture_search_fts f
               LEFT JOIN capture_embeddings ce
                 ON ce.capture_id = f.capture_id
                AND ce.kind = f.kind
                AND ce.section_id = f.section_id"""
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_review_items_table(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(review_items)").fetchall()
    }
    if "view_count" not in existing:
        conn.execute(
            "ALTER TABLE review_items "
            "ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0"
        )
    if "last_viewed" not in existing:
        conn.execute("ALTER TABLE review_items ADD COLUMN last_viewed TEXT")
