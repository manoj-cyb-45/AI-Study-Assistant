#!/usr/bin/env python3
"""Validate environment configuration before starting the application.

Run automatically by start.sh. Exits non-zero with a clear message if any
mandatory environment variable is missing or invalid, so deployment fails
fast instead of crashing later inside the request path.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import get_settings  # noqa: E402
from app.utils.exceptions import ConfigurationError  # noqa: E402


def main() -> int:
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc.message}", file=sys.stderr)
        return 1

    print("Configuration OK:")
    print(f"  Environment:        {settings.environment}")
    print(f"  Database path:      {settings.database_path}")
    print(f"  Upload dir:         {settings.upload_dir}")
    print(f"  GitHub repo:        {settings.github_repo_url} (branch: {settings.github_branch})")
    print(f"  Telegram webhook:   {'enabled' if settings.telegram_use_webhook else 'disabled (polling)'}")
    print(f"  Default LLM model:  {settings.default_llm_model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
