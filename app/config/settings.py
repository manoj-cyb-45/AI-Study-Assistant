"""Application configuration loaded from environment variables.

All configuration is centralized here. Nothing else in the codebase should
call ``os.environ`` directly for application settings — import ``get_settings()``
instead. This keeps validation, defaults, and documentation in one place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

from app.utils.exceptions import ConfigurationError

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"Environment variable {name} must be an integer, got: {raw!r}") from exc


def _parse_admin_ids(raw: str) -> tuple[int, ...]:
    if not raw.strip():
        return ()
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError as exc:
            raise ConfigurationError(f"ADMIN_USER_IDS must be a comma-separated list of integers, got: {raw!r}") from exc
    return tuple(ids)


@dataclass(frozen=True)
class Settings:
    """Immutable application settings, populated once at startup."""

    # --- Telegram ---
    telegram_bot_token: str
    telegram_webhook_url: str
    telegram_webhook_secret: str
    telegram_use_webhook: bool

    # --- OpenRouter / LLM ---
    openrouter_api_key: str
    openrouter_base_url: str
    default_llm_model: str
    llm_temperature: float
    llm_max_tokens: int
    llm_request_timeout_seconds: int
    llm_max_retries: int

    # --- GitHub ---
    github_repo_url: str
    github_branch: str
    github_token: str

    # --- Admin / scheduled sync ---
    admin_user_ids: tuple[int, ...]
    sync_interval_minutes: int

    # --- Storage ---
    database_path: str
    upload_dir: str
    cache_dir: str
    log_dir: str
    backup_dir: str

    # --- Behaviour ---
    max_conversation_history: int
    search_result_limit: int
    search_context_char_limit: int
    session_idle_timeout_minutes: int
    request_timeout_seconds: int
    log_level: str
    environment: str

    # --- FastAPI ---
    api_host: str
    api_port: int

    def validate(self) -> None:
        """Raise ConfigurationError if any mandatory value is missing."""
        mandatory = {
            "TELEGRAM_BOT_TOKEN": self.telegram_bot_token,
            "OPENROUTER_API_KEY": self.openrouter_api_key,
            "GITHUB_REPO_URL": self.github_repo_url,
        }
        missing = [name for name, value in mandatory.items() if not value]
        if missing:
            raise ConfigurationError(
                f"Missing required environment variables: {', '.join(missing)}. "
                "Copy .env.example to .env and fill in the required values."
            )

        if self.telegram_use_webhook and not self.telegram_webhook_url:
            raise ConfigurationError(
                "TELEGRAM_USE_WEBHOOK is true but TELEGRAM_WEBHOOK_URL is not set."
            )

        if self.llm_temperature < 0 or self.llm_temperature > 2:
            raise ConfigurationError("LLM_TEMPERATURE must be between 0 and 2.")

        if self.max_conversation_history < 1:
            raise ConfigurationError("MAX_CONVERSATION_HISTORY must be at least 1.")

        if self.sync_interval_minutes < 0:
            raise ConfigurationError("SYNC_INTERVAL_MINUTES must be zero (disabled) or positive.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load, validate, and cache settings for the lifetime of the process."""
    settings = Settings(
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_webhook_url=os.environ.get("TELEGRAM_WEBHOOK_URL", ""),
        telegram_webhook_secret=os.environ.get("TELEGRAM_WEBHOOK_SECRET", ""),
        telegram_use_webhook=_get_bool("TELEGRAM_USE_WEBHOOK", False),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        openrouter_base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        default_llm_model=os.environ.get("DEFAULT_LLM_MODEL", "openai/gpt-4o-mini"),
        llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.4")),
        llm_max_tokens=_get_int("LLM_MAX_TOKENS", 1500),
        llm_request_timeout_seconds=_get_int("LLM_REQUEST_TIMEOUT_SECONDS", 45),
        llm_max_retries=_get_int("LLM_MAX_RETRIES", 3),
        github_repo_url=os.environ.get("GITHUB_REPO_URL", ""),
        github_branch=os.environ.get("GITHUB_BRANCH", "main"),
        github_token=os.environ.get("GITHUB_TOKEN", ""),
        admin_user_ids=_parse_admin_ids(os.environ.get("ADMIN_USER_IDS", "")),
        sync_interval_minutes=_get_int("SYNC_INTERVAL_MINUTES", 0),
        database_path=os.environ.get("DATABASE_PATH", "data/study_assistant.db"),
        upload_dir=os.environ.get("UPLOAD_DIR", "data/repo"),
        cache_dir=os.environ.get("CACHE_DIR", "data/cache"),
        log_dir=os.environ.get("LOG_DIR", "data/logs"),
        backup_dir=os.environ.get("BACKUP_DIR", "data/backups"),
        max_conversation_history=_get_int("MAX_CONVERSATION_HISTORY", 20),
        search_result_limit=_get_int("SEARCH_RESULT_LIMIT", 8),
        search_context_char_limit=_get_int("SEARCH_CONTEXT_CHAR_LIMIT", 12000),
        session_idle_timeout_minutes=_get_int("SESSION_IDLE_TIMEOUT_MINUTES", 60),
        request_timeout_seconds=_get_int("REQUEST_TIMEOUT_SECONDS", 30),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        environment=os.environ.get("ENVIRONMENT", "development"),
        api_host=os.environ.get("API_HOST", "0.0.0.0"),
        api_port=_get_int("API_PORT", 8000),
    )
    settings.validate()
    return settings
