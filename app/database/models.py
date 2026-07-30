"""Plain dataclasses representing rows in the database.

These are intentionally simple data containers (no ORM) since the project
uses raw SQL via ``app.database.repository`` for full control over the FTS5
queries and incremental sync logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Document:
    id: int | None
    relative_path: str
    file_name: str
    subject: str | None
    module: str | None
    file_hash: str
    repo_version: str | None
    page_count: int
    created_at: str
    updated_at: str
    status: str = "active"


@dataclass
class Section:
    id: int | None
    document_id: int
    page_number: int | None
    heading: str | None
    content: str
    created_at: str


@dataclass
class SearchResult:
    section_id: int
    document_name: str
    relative_path: str
    subject: str | None
    module: str | None
    page_number: int | None
    heading: str | None
    content: str
    # Raw value straight from the database is FTS5's bm25() score (smaller/
    # more negative = better match). SearchEngine.search() overwrites this
    # with a composite re-ranking score before returning results, where
    # *higher* means a better match — see app.search.search_engine._rescore.
    rank: float


@dataclass
class SyncRun:
    id: int | None
    commit_hash: str
    started_at: str
    finished_at: str | None
    files_added: int = 0
    files_updated: int = 0
    files_deleted: int = 0
    files_failed: int = 0
    error_message: str | None = None
    status: str = "in_progress"


@dataclass
class UserSession:
    telegram_user_id: int
    username: str | None
    current_subject: str | None
    current_module: str | None
    preferred_language: str
    answer_detail_level: str
    last_active_at: str
    created_at: str


@dataclass
class ConversationEntry:
    id: int | None
    telegram_user_id: int
    created_at: str
    detected_subject: str | None
    detected_module: str | None
    detected_intent: str | None
    user_question: str
    assistant_response: str
    retrieved_references: str | None
    response_time_ms: int | None
    llm_model: str | None
