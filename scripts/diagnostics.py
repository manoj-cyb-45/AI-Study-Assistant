#!/usr/bin/env python3
"""Print a full diagnostic report of the application's current state.

Useful for quickly checking a deployment without digging through logs.

Usage:
    python scripts/diagnostics.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import get_settings  # noqa: E402
from app.database.connection import get_database  # noqa: E402
from app.database.repository import Repository  # noqa: E402
from app.database.schema import initialize_schema  # noqa: E402
from app.health.health_check import run_health_check  # noqa: E402


def main() -> int:
    settings = get_settings()
    db = get_database(settings.database_path)
    initialize_schema(db)
    repository = Repository(db)

    print("=" * 60)
    print("AI Study Assistant — Diagnostics Report")
    print("=" * 60)

    report = run_health_check(settings, db, repository)
    print(f"Overall status: {report['status']}")
    print(f"Version:        {report['version']}")
    print(f"Environment:    {report['environment']}")
    print()
    for component in report["components"]:
        marker = "OK  " if component["healthy"] else "FAIL"
        print(f"[{marker}] {component['name']:<15} {component['detail']}")

    print()
    print(f"Indexed documents: {repository.document_count()}")
    print(f"Subjects:          {', '.join(repository.list_subjects()) or '(none)'}")

    last_run = repository.get_latest_sync_run()
    if last_run:
        print()
        print("Last sync run:")
        print(f"  Commit:   {last_run.commit_hash[:12]}")
        print(f"  Started:  {last_run.started_at}")
        print(f"  Finished: {last_run.finished_at}")
        print(f"  Added:    {last_run.files_added}")
        print(f"  Updated:  {last_run.files_updated}")
        print(f"  Deleted:  {last_run.files_deleted}")
        print(f"  Failed:   {last_run.files_failed}")
        print(f"  Status:   {last_run.status}")

    return 0 if report["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
