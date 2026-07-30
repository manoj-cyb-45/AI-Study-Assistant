"""SQLite connection management.

SQLite is single-file and does not benefit from a traditional connection
pool, but concurrent access from FastAPI + the Telegram bot's async workers
still needs care. This module opens connections in WAL mode (allowing
concurrent readers alongside a single writer), enables foreign keys, and
exposes a context manager so callers never leak connections or forget to
commit/rollback.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.utils.exceptions import DatabaseError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class Database:
    """Thin wrapper around sqlite3 that centralizes connection settings."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._configure_once()

    def _configure_once(self) -> None:
        """Apply pragmas that must be set outside a transaction."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Failed to initialize SQLite database at {self.db_path}: {exc}") from exc

    def raw_connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection, committing on success and rolling back on error."""
        conn = self.raw_connect()
        try:
            yield conn
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            logger.error("Database operation failed, rolled back: %s", exc)
            raise DatabaseError(str(exc)) from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


_db_instance: Database | None = None


def get_database(db_path: str | None = None) -> Database:
    """Return the process-wide Database singleton, creating it if needed."""
    global _db_instance
    if _db_instance is None:
        if db_path is None:
            from app.config.settings import get_settings

            db_path = get_settings().database_path
        _db_instance = Database(db_path)
    return _db_instance
