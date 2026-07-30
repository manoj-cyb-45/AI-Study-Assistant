#!/usr/bin/env python3
"""Delete rotated log files older than a configurable age.

Usage:
    python scripts/cleanup_logs.py [--max-age-days 30]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete rotated log files older than N days.")
    parser.add_argument("--max-age-days", type=int, default=30)
    args = parser.parse_args()

    settings = get_settings()
    log_dir = Path(settings.log_dir)
    if not log_dir.exists():
        print(f"Log directory {log_dir} does not exist; nothing to do.")
        return 0

    cutoff = time.time() - (args.max_age_days * 86400)
    removed = 0
    for item in log_dir.glob("*.log.*"):
        if item.is_file() and item.stat().st_mtime < cutoff:
            item.unlink()
            removed += 1

    print(f"Removed {removed} rotated log file(s) older than {args.max_age_days} days from {log_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
