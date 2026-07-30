"""Database schema definitions and migration entry point.

All tables are created idempotently with ``CREATE TABLE IF NOT EXISTS`` so
``initialize_schema`` is safe to call on every startup.
"""

from __future__ import annotations

from app.database.connection import Database
from app.utils.logger import get_logger

logger = get_logger(__name__)

_SCHEMA_STATEMENTS = [
    # Repository-level sync metadata (single source of truth for what commit
    # is currently indexed).
    """
    CREATE TABLE IF NOT EXISTS repo_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        last_commit_hash TEXT,
        last_synced_at TEXT
    );
    """,
    # One row per sync run, for observability and debugging.
    """
    CREATE TABLE IF NOT EXISTS sync_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        commit_hash TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        files_added INTEGER DEFAULT 0,
        files_updated INTEGER DEFAULT 0,
        files_deleted INTEGER DEFAULT 0,
        files_failed INTEGER DEFAULT 0,
        error_message TEXT,
        status TEXT NOT NULL DEFAULT 'in_progress'
    );
    """,
    # One row per known PDF document (metadata + processing status).
    """
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        relative_path TEXT NOT NULL UNIQUE,
        file_name TEXT NOT NULL,
        subject TEXT,
        module TEXT,
        file_hash TEXT NOT NULL,
        repo_version TEXT,
        page_count INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active'
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_documents_subject_module ON documents (subject, module);",
    # Section-level extracted content, one row per logical chunk of a
    # document (roughly one row per page, split further on headings).
    """
    CREATE TABLE IF NOT EXISTS sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        page_number INTEGER,
        heading TEXT,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_sections_document_id ON sections (document_id);",
    # FTS5 virtual table mirroring `sections` for full-text search. Kept in
    # sync via triggers so callers never write to it directly. This is a
    # standard (non "external content") FTS5 table: at the 300-500 PDF scale
    # this project targets, the modest storage duplication is a worthwhile
    # trade for simplicity — column values can be read straight back out of
    # sections_fts without wiring up an external content table.
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts USING fts5(
        content,
        heading,
        subject UNINDEXED,
        module UNINDEXED,
        document_name UNINDEXED,
        relative_path UNINDEXED,
        page_number UNINDEXED,
        tokenize='porter unicode61'
    );
    """,
    # Triggers keep sections_fts synchronized with sections + documents.
    """
    CREATE TRIGGER IF NOT EXISTS sections_ai AFTER INSERT ON sections BEGIN
        INSERT INTO sections_fts (rowid, content, heading, subject, module, document_name, relative_path, page_number)
        SELECT new.id, new.content, COALESCE(new.heading, ''), d.subject, d.module, d.file_name, d.relative_path, new.page_number
        FROM documents d WHERE d.id = new.document_id;
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS sections_ad AFTER DELETE ON sections BEGIN
        DELETE FROM sections_fts WHERE rowid = old.id;
    END;
    """,
    # Per-user session state (current subject/module focus, preferences).
    """
    CREATE TABLE IF NOT EXISTS user_sessions (
        telegram_user_id INTEGER PRIMARY KEY,
        username TEXT,
        current_subject TEXT,
        current_module TEXT,
        preferred_language TEXT DEFAULT 'en',
        answer_detail_level TEXT DEFAULT 'standard',
        last_active_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    # Full interaction log, used both for conversation context and history
    # export/search.
    """
    CREATE TABLE IF NOT EXISTS conversation_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        detected_subject TEXT,
        detected_module TEXT,
        detected_intent TEXT,
        user_question TEXT NOT NULL,
        assistant_response TEXT NOT NULL,
        retrieved_references TEXT,
        response_time_ms INTEGER,
        llm_model TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_conv_user_time ON conversation_history (telegram_user_id, created_at);",
]


def initialize_schema(db: Database) -> None:
    """Create every table, index, and trigger if it doesn't already exist."""
    with db.connect() as conn:
        for statement in _SCHEMA_STATEMENTS:
            conn.execute(statement)
        conn.execute(
            "INSERT OR IGNORE INTO repo_state (id, last_commit_hash, last_synced_at) VALUES (1, NULL, NULL);"
        )
    logger.info("Database schema initialized at %s", db.db_path)
