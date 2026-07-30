#!/usr/bin/env python3
"""Rebuild the entire database from scratch.

Deletes the existing SQLite database file, recreates the schema, and
force-resyncs and reprocesses every PDF in the configured GitHub
repository. Use this after a schema change or if the index is suspected to
be corrupted or badly out of sync.

Usage:
    python scripts/rebuild_database.py --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import get_settings  # noqa: E402
from app.database.connection import Database  # noqa: E402
from app.database.repository import Repository  # noqa: E402
from app.database.schema import initialize_schema  # noqa: E402
from app.github_sync.sync_service import GitHubSyncService  # noqa: E402
from app.pdf_processing.indexing_service import IndexingService  # noqa: E402
from app.utils.exceptions import StudyAssistantError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild the database from scratch.")
    parser.add_argument("--yes", action="store_true", help="Confirm destructive rebuild without prompting.")
    args = parser.parse_args()

    settings = get_settings()
    db_path = Path(settings.database_path)

    if not args.yes:
        response = input(f"This will delete {db_path} and rebuild it from GitHub. Continue? [y/N] ")
        if response.strip().lower() != "y":
            print("Aborted.")
            return 1

    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db_path) + suffix)
        if candidate.exists():
            candidate.unlink()
            print(f"Removed {candidate}")

    db = Database(settings.database_path)
    initialize_schema(db)
    repository = Repository(db)

    sync_service = GitHubSyncService(
        repository,
        repo_url=settings.github_repo_url,
        branch=settings.github_branch,
        github_token=settings.github_token,
        upload_dir=settings.upload_dir,
        request_timeout_seconds=settings.request_timeout_seconds,
    )
    indexing_service = IndexingService(repository, sync_service, settings.upload_dir)

    try:
        summary = indexing_service.sync_and_index(force=True)
    except StudyAssistantError as exc:
        print(f"Rebuild failed: {exc.message}", file=sys.stderr)
        return 1
    finally:
        sync_service.close()

    print(f"Rebuild complete: {summary['indexed']} indexed, {summary['failed']} failed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
