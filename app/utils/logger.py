"""Centralized logging configuration.

All modules obtain their logger through ``get_logger(__name__)``. Log files
rotate automatically so disk usage stays bounded, and formatting is
consistent across the whole application. Sensitive values (API keys,
tokens, raw prompts) must never be passed into these loggers by callers.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

_CONFIGURED = False
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(log_dir: str = "data/logs", log_level: str = "INFO") -> None:
    """Configure the root logger once for the whole process.

    Safe to call multiple times; only the first call takes effect.

    Args:
        log_dir: Directory where rotating log files are written.
        log_level: One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    level = getattr(logging, log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # Rotating file handler: 5 MB per file, keep 10 backups.
    file_handler = logging.handlers.RotatingFileHandler(
        filename=os.path.join(log_dir, "app.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    # Separate error-only file for quick incident triage.
    error_handler = logging.handlers.RotatingFileHandler(
        filename=os.path.join(log_dir, "errors.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(error_handler)
    root.addHandler(console_handler)

    # Dedicated per-subsystem log files, in addition to the combined app.log
    # above (these loggers still propagate to the root handlers too — this
    # only adds a focused, easier-to-tail file per subsystem for debugging).
    _SUBSYSTEM_LOG_FILES = {
        "app.telegram_bot": "bot.log",
        "app.search": "search.log",
        "app.github_sync": "github.log",
        "app.database": "database.log",
        "app.llm": "openrouter.log",
    }
    for logger_name, filename in _SUBSYSTEM_LOG_FILES.items():
        subsystem_handler = logging.handlers.RotatingFileHandler(
            filename=os.path.join(log_dir, filename),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        subsystem_handler.setFormatter(formatter)
        subsystem_handler.setLevel(level)
        subsystem_logger = logging.getLogger(logger_name)
        subsystem_logger.addHandler(subsystem_handler)
        # propagate stays True (default) so entries also land in app.log/console.

    # Third-party libraries are noisy at DEBUG/INFO; keep them quieter.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger, configuring logging on first use."""
    if not _CONFIGURED:
        configure_logging(
            log_dir=os.environ.get("LOG_DIR", "data/logs"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )
    return logging.getLogger(name)
