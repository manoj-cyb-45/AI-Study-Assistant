"""Application entrypoint.

Boots the FastAPI app: loads and validates configuration, initializes the
SQLite database and schema, performs an initial GitHub sync + index pass,
constructs the Telegram bot, and starts it in either polling mode (for
local development) or webhook mode (mounted as a FastAPI route, for
production). A `/health` endpoint reports the status of every subsystem.

If SYNC_INTERVAL_MINUTES is set, a background task also re-syncs the
repository on that interval without blocking FastAPI or the Telegram bot's
own event loop (the actual sync/index work runs in a worker thread).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from telegram import Update

from app.config.settings import get_settings
from app.database.connection import get_database
from app.database.repository import Repository
from app.github_sync.sync_service import GitHubSyncService
from app.health.health_check import run_health_check
from app.llm.openrouter_service import OpenRouterService
from app.middleware.logging_middleware import (
    RequestLoggingMiddleware,
    study_assistant_exception_handler,
    unhandled_exception_handler,
)
from app.pdf_processing.indexing_service import IndexingService
from app.search.search_engine import SearchEngine
from app.telegram_bot.bot import build_application, run_webhook
from app.utils.exceptions import StudyAssistantError
from app.utils.logger import configure_logging, get_logger

logger = get_logger(__name__)


async def _scheduled_sync_loop(indexing_service: IndexingService, cache, interval_minutes: int) -> None:
    """Background task: re-sync the repository every ``interval_minutes``.

    Runs the (blocking) sync/index work in a worker thread via
    ``asyncio.to_thread`` so it never stalls FastAPI's event loop or the
    Telegram bot's own polling/webhook processing.
    """
    interval_seconds = interval_minutes * 60
    logger.info("Scheduled sync enabled: checking GitHub every %d minute(s).", interval_minutes)
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            summary = await asyncio.to_thread(indexing_service.sync_and_index, force=False)
            if summary["status"] == "unchanged":
                logger.info("Scheduled sync: repository already up-to-date.")
            else:
                cache.clear()
                logger.info(
                    "Scheduled sync: indexed=%d added=%d updated=%d deleted=%d failed=%d",
                    summary["indexed"], summary.get("added", 0), summary.get("updated", 0),
                    summary["deleted"], summary["failed"],
                )
        except StudyAssistantError as exc:
            logger.error("Scheduled sync failed, will retry on the next interval: %s", exc)
        except Exception:
            logger.exception("Unexpected error during scheduled sync")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_dir, settings.log_level)
    logger.info("Starting AI Study Assistant (environment=%s)", settings.environment)

    db = get_database(settings.database_path)
    from app.database.schema import initialize_schema

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
    search_engine = SearchEngine(
        db, result_limit=settings.search_result_limit, context_char_limit=settings.search_context_char_limit
    )
    llm_service = OpenRouterService(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        default_model=settings.default_llm_model,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout_seconds=settings.llm_request_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )

    # Startup synchronization: check GitHub, download changes, index modified
    # PDFs. The Telegram bot is only built/started below, after this
    # completes — a transient GitHub outage is logged and does not prevent
    # startup (existing indexed content keeps serving), matching the
    # project's graceful-degradation design.
    try:
        logger.info("Running startup GitHub sync...")
        indexing_service.sync_and_index(force=False)
    except StudyAssistantError as exc:
        logger.error("Startup GitHub sync failed; continuing with previously indexed content: %s", exc)

    telegram_app = build_application(
        settings,
        db=db,
        repository=repository,
        search_engine=search_engine,
        llm_service=llm_service,
        indexing_service=indexing_service,
    )

    app.state.settings = settings
    app.state.db = db
    app.state.repository = repository
    app.state.telegram_app = telegram_app

    await telegram_app.initialize()
    await telegram_app.start()

    if settings.telegram_use_webhook:
        await run_webhook(telegram_app, settings)
    else:
        await telegram_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Telegram bot started in polling mode")

    scheduled_sync_task: asyncio.Task | None = None
    if settings.sync_interval_minutes > 0:
        from app.caching.cache_service import cache as shared_cache

        scheduled_sync_task = asyncio.create_task(
            _scheduled_sync_loop(indexing_service, shared_cache, settings.sync_interval_minutes)
        )

    try:
        yield
    finally:
        logger.info("Shutting down AI Study Assistant...")
        if scheduled_sync_task is not None:
            scheduled_sync_task.cancel()
        if telegram_app.updater and telegram_app.updater.running:
            await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()
        sync_service.close()
        llm_service.close()


app = FastAPI(title="AI Study Assistant", version="1.0.0", lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware)
app.add_exception_handler(StudyAssistantError, study_assistant_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)


@app.get("/health")
async def health(request: Request) -> dict:
    return run_health_check(request.app.state.settings, request.app.state.db, request.app.state.repository)


@app.get("/")
async def root() -> dict:
    return {"name": "AI Study Assistant", "status": "running"}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> Response:
    """Receive Telegram updates when running in webhook mode."""
    settings = request.app.state.settings
    if settings.telegram_webhook_secret:
        header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if header_secret != settings.telegram_webhook_secret:
            return Response(status_code=401, content="Invalid secret token")

    telegram_app = request.app.state.telegram_app
    payload = await request.json()
    update = Update.de_json(payload, telegram_app.bot)
    await telegram_app.process_update(update)
    return Response(status_code=200)
