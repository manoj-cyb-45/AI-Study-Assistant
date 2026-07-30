#!/usr/bin/env python3
"""Manually trigger a GitHub sync + index pass outside of the running server.

Usage:
    python scripts/sync_repository.py [--force]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import get_settings  # noqa: E402
from app.database.connection import get_database  # noqa: E402
from app.database.repository import Repository  # noqa: E402
from app.database.schema import initialize_schema  # noqa: E402
from app.github_sync.sync_service import GitHubSyncService  # noqa: E402
from app.pdf_processing.indexing_service import IndexingService  # noqa: E402
from app.utils.exceptions import StudyAssistantError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync and index study materials from GitHub.")
    parser.add_argument("--force", action="store_true", help="Reprocess every file even if unchanged.")
    args = parser.parse_args()

    settings = get_settings()
    db = get_database(settings.database_path)
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
        summary = indexing_service.sync_and_index(force=args.force)
    except StudyAssistantError as exc:
        print(f"Sync failed: {exc.message}", file=sys.stderr)
        return 1
    finally:
        sync_service.close()

    print(f"Status:  {summary['status']}")
    print(f"Commit:  {summary['commit_hash']}")
    print(f"Indexed: {summary['indexed']}")
    print(f"Deleted: {summary['deleted']}")
    print(f"Failed:  {summary['failed']}")
    if summary.get("failed_files"):
        print("Failed files:")
        for path, error in summary["failed_files"]:
            print(f"  - {path}: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
