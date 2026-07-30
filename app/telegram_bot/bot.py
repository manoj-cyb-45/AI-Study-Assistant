"""Telegram bot application factory.

Builds a python-telegram-bot ``Application``, registers all command and
message handlers, and stores shared services in ``bot_data`` so handlers
can reach them without global state. Supports both polling (local
development) and webhook (production) modes, selected via configuration.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.caching.cache_service import cache
from app.config.settings import Settings
from app.database.connection import Database
from app.database.repository import Repository
from app.llm.openrouter_service import OpenRouterService
from app.pdf_processing.indexing_service import IndexingService
from app.search.search_engine import SearchEngine
from app.telegram_bot import handlers
from app.telegram_bot.conversation_manager import ConversationManager
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def _global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all so an unexpected exception never crashes the bot process."""
    logger.error("Unhandled exception in Telegram update handling: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Something went wrong on my end. Please try again in a moment."
            )
        except Exception:
            logger.error("Failed to notify user about the earlier error.")


def build_application(
    settings: Settings,
    *,
    db: Database,
    repository: Repository,
    search_engine: SearchEngine,
    llm_service: OpenRouterService,
    indexing_service: IndexingService,
) -> Application:
    """Construct and configure the Telegram Application with all dependencies wired in."""
    application = Application.builder().token(settings.telegram_bot_token).build()

    conversation_manager = ConversationManager(
        repository, search_engine, llm_service, cache, max_history=settings.max_conversation_history
    )

    application.bot_data.update(
        {
            "db": db,
            "repository": repository,
            "search_engine": search_engine,
            "llm_service": llm_service,
            "indexing_service": indexing_service,
            "conversation_manager": conversation_manager,
            "cache": cache,
            "settings": settings,
        }
    )

    application.add_handler(CommandHandler("start", handlers.start_command))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(CommandHandler("about", handlers.about_command))
    application.add_handler(CommandHandler("subjects", handlers.subjects_command))
    application.add_handler(CommandHandler("modules", handlers.modules_command))
    application.add_handler(CommandHandler("history", handlers.history_command))
    application.add_handler(CommandHandler("clear", handlers.clear_command))
    application.add_handler(CommandHandler("refresh", handlers.refresh_command))
    application.add_handler(CommandHandler("settings", handlers.settings_command))
    # Admin-only commands — see handlers._reject_if_not_admin / ADMIN_USER_IDS.
    application.add_handler(CommandHandler("sync", handlers.sync_command))
    application.add_handler(CommandHandler("syncstatus", handlers.syncstatus_command))
    application.add_handler(CommandHandler("reindex", handlers.reindex_command))
    application.add_handler(CommandHandler("status", handlers.status_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_message))
    application.add_error_handler(_global_error_handler)

    return application


def run_polling(application: Application) -> None:
    """Run the bot using long polling — suitable for local development."""
    logger.info("Starting Telegram bot in polling mode")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


async def run_webhook(application: Application, settings: Settings) -> None:
    """Configure the bot's webhook — used when running behind FastAPI in production."""
    webhook_url = settings.telegram_webhook_url.rstrip("/") + "/telegram/webhook"
    await application.bot.set_webhook(
        url=webhook_url,
        secret_token=settings.telegram_webhook_secret or None,
        allowed_updates=Update.ALL_TYPES,
    )
    logger.info("Telegram webhook configured at %s", webhook_url)
