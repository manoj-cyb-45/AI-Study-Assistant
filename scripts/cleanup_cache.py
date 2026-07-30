#!/usr/bin/env python3
"""Clean up stale cache entries and rotated log files.

Usage:
    python scripts/cleanup_cache.py           # clears the in-process cache
    python scripts/cleanup_cache.py --logs    # also deletes rotated .log.N files older than 30 days
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import get_settings  # noqa: E402

_DEFAULT_MAX_AGE_DAYS = 30


def clean_cache_dir(cache_dir: str) -> int:
    path = Path(cache_dir)
    if not path.exists():
        return 0
    removed = 0
    for item in path.glob("**/*"):
        if item.is_file():
            item.unlink()
            removed += 1
    return removed


def clean_old_logs(log_dir: str, max_age_days: int) -> int:
    path = Path(log_dir)
    if not path.exists():
        return 0
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    for item in path.glob("*.log.*"):
        if item.is_file() and item.stat().st_mtime < cutoff:
            item.unlink()
            removed += 1
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean up cache and old rotated logs.")
    parser.add_argument("--logs", action="store_true", help="Also clean up old rotated log files.")
    parser.add_argument("--max-age-days", type=int, default=_DEFAULT_MAX_AGE_DAYS)
    args = parser.parse_args()

    settings = get_settings()
    removed_cache = clean_cache_dir(settings.cache_dir)
    print(f"Removed {removed_cache} cache file(s) from {settings.cache_dir}")

    if args.logs:
        removed_logs = clean_old_logs(settings.log_dir, args.max_age_days)
        print(f"Removed {removed_logs} rotated log file(s) older than {args.max_age_days} days")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
