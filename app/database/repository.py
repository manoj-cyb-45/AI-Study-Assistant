"""Data-access layer.

Every SQL statement in the application lives here (or in ``search_engine.py``
for read-heavy FTS queries). Services never issue raw SQL themselves; they
call these repository methods. This keeps schema knowledge in one place and
makes incremental-sync logic easy to test in isolation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.database.connection import Database
from app.database.models import ConversationEntry, Document, SyncRun, UserSession
from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Repository:
    """Facade over all database tables used by the application."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # repo_state / sync_history
    # ------------------------------------------------------------------
    def get_last_commit_hash(self) -> str | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT last_commit_hash FROM repo_state WHERE id = 1").fetchone()
            return row["last_commit_hash"] if row else None

    def set_last_commit_hash(self, commit_hash: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE repo_state SET last_commit_hash = ?, last_synced_at = ? WHERE id = 1",
                (commit_hash, _now()),
            )

    def start_sync_run(self, commit_hash: str) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO sync_history (commit_hash, started_at, status) VALUES (?, ?, 'in_progress')",
                (commit_hash, _now()),
            )
            return int(cursor.lastrowid)

    def finish_sync_run(
        self,
        run_id: int,
        *,
        files_added: int,
        files_updated: int,
        files_deleted: int,
        files_failed: int,
        status: str,
        error_message: str | None = None,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE sync_history
                SET finished_at = ?, files_added = ?, files_updated = ?, files_deleted = ?,
                    files_failed = ?, status = ?, error_message = ?
                WHERE id = ?
                """,
                (_now(), files_added, files_updated, files_deleted, files_failed, status, error_message, run_id),
            )

    def get_latest_sync_run(self) -> SyncRun | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sync_history ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            return SyncRun(**dict(row))

    # ------------------------------------------------------------------
    # documents
    # ------------------------------------------------------------------
    def get_document_by_path(self, relative_path: str) -> Document | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE relative_path = ?", (relative_path,)
            ).fetchone()
            return Document(**dict(row)) if row else None

    def list_all_document_paths(self) -> dict[str, str]:
        """Return {relative_path: file_hash} for every active document."""
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT relative_path, file_hash FROM documents WHERE status = 'active'"
            ).fetchall()
            return {row["relative_path"]: row["file_hash"] for row in rows}

    def upsert_document(
        self,
        *,
        relative_path: str,
        file_name: str,
        subject: str | None,
        module: str | None,
        file_hash: str,
        repo_version: str | None,
        page_count: int,
    ) -> int:
        now = _now()
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM documents WHERE relative_path = ?", (relative_path,)
            ).fetchone()
            if existing:
                doc_id = int(existing["id"])
                conn.execute(
                    """
                    UPDATE documents
                    SET file_name = ?, subject = ?, module = ?, file_hash = ?,
                        repo_version = ?, page_count = ?, updated_at = ?, status = 'active'
                    WHERE id = ?
                    """,
                    (file_name, subject, module, file_hash, repo_version, page_count, now, doc_id),
                )
                # Existing sections are replaced wholesale by the PDF service
                # calling replace_sections() after this, so we don't delete
                # them here to keep this method single-purpose.
                return doc_id

            cursor = conn.execute(
                """
                INSERT INTO documents
                    (relative_path, file_name, subject, module, file_hash, repo_version,
                     page_count, created_at, updated_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (relative_path, file_name, subject, module, file_hash, repo_version, page_count, now, now),
            )
            return int(cursor.lastrowid)

    def replace_sections(self, document_id: int, sections: list[dict[str, Any]]) -> None:
        """Delete all existing sections for a document and insert new ones."""
        now = _now()
        with self.db.connect() as conn:
            conn.execute("DELETE FROM sections WHERE document_id = ?", (document_id,))
            conn.executemany(
                "INSERT INTO sections (document_id, page_number, heading, content, created_at) VALUES (?, ?, ?, ?, ?)",
                [
                    (document_id, s.get("page_number"), s.get("heading"), s["content"], now)
                    for s in sections
                ],
            )

    def mark_document_deleted(self, relative_path: str) -> None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM documents WHERE relative_path = ?", (relative_path,)
            ).fetchone()
            if not row:
                return
            doc_id = int(row["id"])
            conn.execute("DELETE FROM sections WHERE document_id = ?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    def list_subjects(self) -> list[str]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT subject FROM documents WHERE subject IS NOT NULL AND status = 'active' ORDER BY subject"
            ).fetchall()
            return [row["subject"] for row in rows]

    def list_modules(self, subject: str | None = None) -> list[str]:
        with self.db.connect() as conn:
            if subject:
                rows = conn.execute(
                    "SELECT DISTINCT module FROM documents WHERE module IS NOT NULL AND subject = ? AND status = 'active' ORDER BY module",
                    (subject,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT DISTINCT module FROM documents WHERE module IS NOT NULL AND status = 'active' ORDER BY module"
                ).fetchall()
            return [row["module"] for row in rows]

    def document_count(self) -> int:
        with self.db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM documents WHERE status = 'active'").fetchone()
            return int(row["c"])

    def get_indexed_page_total(self) -> int:
        """Total page_count summed across all indexed documents (for /syncstatus)."""
        with self.db.connect() as conn:
            row = conn.execute("SELECT COALESCE(SUM(page_count), 0) AS total FROM documents WHERE status = 'active'").fetchone()
            return int(row["total"])

    def delete_all_documents(self) -> int:
        """Delete every document (cascades to sections and sections_fts). Used by /reindex."""
        with self.db.connect() as conn:
            cursor = conn.execute("DELETE FROM documents")
            return cursor.rowcount

    def list_local_relative_paths(self) -> list[str]:
        """Relative paths of every currently-indexed document (for reconciling with disk)."""
        with self.db.connect() as conn:
            rows = conn.execute("SELECT relative_path FROM documents WHERE status = 'active'").fetchall()
            return [row["relative_path"] for row in rows]

    # ------------------------------------------------------------------
    # user_sessions
    # ------------------------------------------------------------------
    def get_or_create_session(self, telegram_user_id: int, username: str | None) -> UserSession:
        now = _now()
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_sessions WHERE telegram_user_id = ?", (telegram_user_id,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE user_sessions SET last_active_at = ?, username = ? WHERE telegram_user_id = ?",
                    (now, username, telegram_user_id),
                )
                data = dict(row)
                data["last_active_at"] = now
                data["username"] = username
                return UserSession(**data)

            conn.execute(
                """
                INSERT INTO user_sessions
                    (telegram_user_id, username, current_subject, current_module,
                     preferred_language, answer_detail_level, last_active_at, created_at)
                VALUES (?, ?, NULL, NULL, 'en', 'standard', ?, ?)
                """,
                (telegram_user_id, username, now, now),
            )
            return UserSession(
                telegram_user_id=telegram_user_id,
                username=username,
                current_subject=None,
                current_module=None,
                preferred_language="en",
                answer_detail_level="standard",
                last_active_at=now,
                created_at=now,
            )

    def update_session_focus(self, telegram_user_id: int, *, subject: str | None = None, module: str | None = None) -> None:
        with self.db.connect() as conn:
            if subject is not None:
                conn.execute(
                    "UPDATE user_sessions SET current_subject = ?, last_active_at = ? WHERE telegram_user_id = ?",
                    (subject, _now(), telegram_user_id),
                )
            if module is not None:
                conn.execute(
                    "UPDATE user_sessions SET current_module = ?, last_active_at = ? WHERE telegram_user_id = ?",
                    (module, _now(), telegram_user_id),
                )

    def clear_expired_sessions(self, idle_timeout_minutes: int) -> int:
        """Clear focus (subject/module) for sessions idle beyond the timeout.

        Conversation history is intentionally preserved; only the "current
        focus" pointer is reset so a stale topic doesn't leak into a new
        conversation.
        """
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE user_sessions
                SET current_subject = NULL, current_module = NULL
                WHERE current_subject IS NOT NULL
                  AND datetime(last_active_at) < datetime('now', ?)
                """,
                (f"-{idle_timeout_minutes} minutes",),
            )
            return cursor.rowcount

    # ------------------------------------------------------------------
    # conversation_history
    # ------------------------------------------------------------------
    def add_conversation_entry(self, entry: ConversationEntry) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO conversation_history
                    (telegram_user_id, created_at, detected_subject, detected_module, detected_intent,
                     user_question, assistant_response, retrieved_references, response_time_ms, llm_model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.telegram_user_id,
                    entry.created_at,
                    entry.detected_subject,
                    entry.detected_module,
                    entry.detected_intent,
                    entry.user_question,
                    entry.assistant_response,
                    entry.retrieved_references,
                    entry.response_time_ms,
                    entry.llm_model,
                ),
            )
            return int(cursor.lastrowid)

    def get_recent_history(self, telegram_user_id: int, limit: int) -> list[ConversationEntry]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM conversation_history
                WHERE telegram_user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (telegram_user_id, limit),
            ).fetchall()
            entries = [ConversationEntry(**dict(row)) for row in rows]
            entries.reverse()  # chronological order
            return entries

    def clear_history(self, telegram_user_id: int) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM conversation_history WHERE telegram_user_id = ?", (telegram_user_id,)
            )
            return cursor.rowcount

    def search_history(self, telegram_user_id: int, keyword: str, limit: int = 20) -> list[ConversationEntry]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM conversation_history
                WHERE telegram_user_id = ? AND (user_question LIKE ? OR assistant_response LIKE ?)
                ORDER BY id DESC
                LIMIT ?
                """,
                (telegram_user_id, f"%{keyword}%", f"%{keyword}%", limit),
            ).fetchall()
            return [ConversationEntry(**dict(row)) for row in rows]

    def export_history(self, telegram_user_id: int) -> list[dict[str, Any]]:
        entries = self.get_recent_history(telegram_user_id, limit=10_000)
        return [
            {
                "timestamp": e.created_at,
                "subject": e.detected_subject,
                "module": e.detected_module,
                "question": e.user_question,
                "response": e.assistant_response,
                "references": json.loads(e.retrieved_references) if e.retrieved_references else [],
            }
            for e in entries
        ]
