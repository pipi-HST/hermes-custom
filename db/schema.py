"""SQLite schema for captures database.

Text content (title, summary, tags, etc.) is now stored in Markdown
frontmatter.  SQLite only keeps routing fields and review state.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from db.concurrency import SerializedConnection, transaction

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
    content_markdown TEXT NOT NULL DEFAULT '',
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
    # The whole DDL batch runs in one explicit transaction so an interrupted
    # upgrade cannot leave the database half-migrated (e.g. rows parked in
    # ``captures_new`` after ``captures`` was already dropped).  PRAGMAs above
    # stay outside: journal mode cannot be changed inside a transaction.
    with transaction(conn):
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
        _migrate_timestamp_formats(conn)
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


def _migrate_timestamp_formats(conn: sqlite3.Connection) -> None:
    """Normalise SQLite-default timestamps ("YYYY-MM-DD HH:MM:SS") to ISO-8601.

    Timestamps written by Python already use ``datetime.isoformat()``; rows that
    kept the SQLite ``datetime('now')`` default were a different format, which
    forced ``replace(substr(...))`` sorting hacks in the repositories.  This
    migration is idempotent and only touches the legacy space-separated form, so
    it runs on every ``init_db`` instead of being gated on a schema version: the
    scan is cheap (large vaults measure well under 100 ms for 50k rows) and it
    keeps re-normalising rows written by code paths that still rely on the SQL
    default.  Values that match the legacy shape but are not real timestamps
    (e.g. ``0000-00-00 00:00:00``) keep their original text: ``strftime``
    returns NULL for those, and writing NULL aborted ``init_db`` on NOT NULL
    columns or silently dropped data on nullable ones.
    """
    tables = [
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    for table in tables:
        columns = {
            str(row["name"])
            for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        }
        for column in columns & _TIMESTAMP_COLUMNS:
            conn.execute(
                f'UPDATE "{table}" '
                f'SET "{column}" = COALESCE(strftime(?, "{column}"), "{column}") '
                f'WHERE "{column}" GLOB ?',
                ("%Y-%m-%dT%H:%M:%SZ", "????-??-?? ??:??:??*"),
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
                "ADD COLUMN schema_version TEXT NOT NULL DEFAULT 'garden_card.v1'"
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
    # Backfill card bodies written by the demo seeder/older payloads.  Empty
    # rows are skipped so init_db stays cheap after the first migration.
    try:
        conn.execute(
            "UPDATE garden_cards "
            "SET content_markdown = json_extract(payload_json, '$.content_markdown') "
            "WHERE COALESCE(content_markdown, '') = '' "
            "  AND COALESCE(json_extract(payload_json, '$.content_markdown'), '') != ''"
        )
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
