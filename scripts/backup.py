#!/usr/bin/env python3
"""Create and restore timestamped backups of the database and study materials.

Usage:
    python scripts/backup.py create
    python scripts/backup.py restore --file data/backups/backup_20260101_120000.zip
    python scripts/backup.py list
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import get_settings  # noqa: E402


def _validate_backup(zip_path: Path) -> bool:
    """Confirm the archive is a well-formed zip and contains a valid SQLite file."""
    if not zipfile.is_zipfile(zip_path):
        return False
    with zipfile.ZipFile(zip_path) as archive:
        bad_file = archive.testzip()
        if bad_file is not None:
            print(f"Corrupted entry in archive: {bad_file}")
            return False
        db_entries = [n for n in archive.namelist() if n.endswith(".db")]
        if not db_entries:
            print("Archive does not contain a database file.")
            return False
    return True


def create_backup() -> int:
    settings = get_settings()
    backup_dir = Path(settings.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_path = backup_dir / f"backup_{timestamp}.zip"

    db_path = Path(settings.database_path)
    upload_dir = Path(settings.upload_dir)

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        if db_path.exists():
            # Use SQLite's own backup API for a consistent snapshot even if
            # the database is open elsewhere.
            snapshot_path = backup_dir / f"_snapshot_{timestamp}.db"
            source_conn = sqlite3.connect(db_path)
            dest_conn = sqlite3.connect(snapshot_path)
            with dest_conn:
                source_conn.backup(dest_conn)
            source_conn.close()
            dest_conn.close()
            archive.write(snapshot_path, arcname=db_path.name)
            snapshot_path.unlink()
        else:
            print(f"Warning: database file {db_path} not found; skipping.")

        if upload_dir.exists():
            for file_path in upload_dir.rglob("*.pdf"):
                archive.write(file_path, arcname=str(Path("repo") / file_path.relative_to(upload_dir)))

    if not _validate_backup(archive_path):
        print("Backup validation failed after creation!", file=sys.stderr)
        return 1

    print(f"Backup created: {archive_path}")
    return 0


def restore_backup(file_path: str) -> int:
    settings = get_settings()
    archive_path = Path(file_path)

    if not archive_path.exists():
        print(f"Backup file not found: {archive_path}", file=sys.stderr)
        return 1
    if not _validate_backup(archive_path):
        print("Backup failed validation; aborting restore.", file=sys.stderr)
        return 1

    db_path = Path(settings.database_path)
    upload_dir = Path(settings.upload_dir)

    if db_path.exists():
        safety_copy = db_path.with_suffix(".db.before_restore")
        shutil.copy2(db_path, safety_copy)
        print(f"Existing database backed up to {safety_copy}")

    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            if name.endswith(".db"):
                with archive.open(name) as source, open(db_path, "wb") as dest:
                    shutil.copyfileobj(source, dest)
            elif name.startswith("repo/"):
                target = upload_dir / Path(name).relative_to("repo")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, open(target, "wb") as dest:
                    shutil.copyfileobj(source, dest)

    print(f"Restore complete from {archive_path}")
    return 0


def list_backups() -> int:
    settings = get_settings()
    backup_dir = Path(settings.backup_dir)
    if not backup_dir.exists():
        print("No backups found.")
        return 0
    backups = sorted(backup_dir.glob("backup_*.zip"))
    if not backups:
        print("No backups found.")
        return 0
    for backup in backups:
        size_mb = backup.stat().st_size / (1024 * 1024)
        print(f"{backup.name}  ({size_mb:.1f} MB)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup and restore utility.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("create")
    subparsers.add_parser("list")
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--file", required=True)

    args = parser.parse_args()

    if args.command == "create":
        return create_backup()
    if args.command == "list":
        return list_backups()
    if args.command == "restore":
        return restore_backup(args.file)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
