#!/usr/bin/env python3
"""Optimize the SQLite database: rebuild the FTS5 index and reclaim disk space.

Usage:
    python scripts/optimize_database.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import get_settings  # noqa: E402
from app.database.connection import get_database  # noqa: E402


def main() -> int:
    settings = get_settings()
    db = get_database(settings.database_path)

    with db.connect() as conn:
        print("Optimizing FTS5 index...")
        conn.execute("INSERT INTO sections_fts(sections_fts) VALUES ('optimize');")
        print("Running ANALYZE...")
        conn.execute("ANALYZE;")

    # VACUUM cannot run inside a transaction, so use a dedicated connection.
    raw_conn = db.raw_connect()
    try:
        print("Running VACUUM (this may take a moment)...")
        raw_conn.execute("VACUUM;")
        raw_conn.commit()
    finally:
        raw_conn.close()

    print("Database optimization complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
