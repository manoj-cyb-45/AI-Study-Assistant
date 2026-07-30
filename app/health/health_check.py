"""Health check service.

Aggregates the status of every subsystem the application depends on so a
single ``/health`` endpoint can be used by Docker healthchecks, Render, or
any external uptime monitor.
"""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from app.config.settings import Settings
from app.database.connection import Database
from app.database.repository import Repository

APP_VERSION = "1.0.0"


@dataclass
class ComponentStatus:
    name: str
    healthy: bool
    detail: str


def _check_database(db: Database, repository: Repository) -> ComponentStatus:
    try:
        count = repository.document_count()
        return ComponentStatus("database", True, f"{count} documents indexed")
    except Exception as exc:  # noqa: BLE001 - health checks must never raise
        return ComponentStatus("database", False, str(exc))


def _check_github_sync(repository: Repository) -> ComponentStatus:
    try:
        last_run = repository.get_latest_sync_run()
        if last_run is None:
            return ComponentStatus("github_sync", True, "no sync has run yet")
        healthy = last_run.status in {"completed", "completed_with_errors"}
        return ComponentStatus("github_sync", healthy, f"last status: {last_run.status}")
    except Exception as exc:  # noqa: BLE001
        return ComponentStatus("github_sync", False, str(exc))


def _check_openrouter_config(settings: Settings) -> ComponentStatus:
    healthy = bool(settings.openrouter_api_key)
    return ComponentStatus("openrouter", healthy, "configured" if healthy else "OPENROUTER_API_KEY not set")


def _check_telegram_config(settings: Settings) -> ComponentStatus:
    healthy = bool(settings.telegram_bot_token)
    return ComponentStatus("telegram", healthy, "configured" if healthy else "TELEGRAM_BOT_TOKEN not set")


def _check_disk_space(settings: Settings) -> ComponentStatus:
    try:
        path = Path(settings.upload_dir or ".").resolve()
        while not path.exists() and path.parent != path:
            path = path.parent
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        healthy = free_gb > 0.5
        return ComponentStatus("disk_space", healthy, f"{free_gb:.2f} GB free")
    except OSError as exc:
        return ComponentStatus("disk_space", False, str(exc))


def _app_version() -> str:
    try:
        return version("ai-study-assistant")
    except PackageNotFoundError:
        return APP_VERSION


def run_health_check(settings: Settings, db: Database, repository: Repository) -> dict:
    """Run all component checks and return an aggregate health report."""
    components = [
        _check_database(db, repository),
        _check_github_sync(repository),
        _check_openrouter_config(settings),
        _check_telegram_config(settings),
        _check_disk_space(settings),
    ]
    overall_healthy = all(c.healthy for c in components)
    return {
        "status": "healthy" if overall_healthy else "degraded",
        "version": _app_version(),
        "environment": settings.environment,
        "components": [asdict(c) for c in components],
    }
