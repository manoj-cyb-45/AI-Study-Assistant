"""Telegram bot command and message handlers.

Handlers stay thin: they parse the incoming update, delegate to the
appropriate service (held in ``context.application.bot_data``), and format
the reply. All business logic lives in the service layer so it can be
tested without spinning up python-telegram-bot at all.
"""

from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.config.settings import Settings
from app.formatting.response_formatter import format_response
from app.health.health_check import run_health_check
from app.telegram_bot.conversation_manager import ConversationManager
from app.utils.exceptions import LLMServiceError, StudyAssistantError
from app.utils.logger import get_logger

logger = get_logger(__name__)

_UNAUTHORIZED_MESSAGE = "You are not authorized to use this command."

_WELCOME_MESSAGE = (
    "*Welcome to your AI Study Assistant!* 🎓\n\n"
    "Ask me anything about your course material — explanations, summaries, "
    "revision notes, interview questions, MCQs, or programming problems.\n\n"
    "Just type your question naturally, e.g.:\n"
    "_\"Explain deadlock in operating systems\"_\n"
    "_\"Give 5 MCQs on binary trees\"_\n"
    "_\"Compare TCP and UDP\"_\n\n"
    "Type /help to see all available commands."
)

_HELP_MESSAGE = (
    "*Available commands:*\n"
    "/start - Welcome message\n"
    "/help - Show this help message\n"
    "/about - About this bot\n"
    "/subjects - List available subjects\n"
    "/modules - List modules for your current subject\n"
    "/history - Show your recent questions\n"
    "/clear - Clear your conversation history\n"
    "/refresh - Re-sync study materials from GitHub\n"
    "/settings - View and adjust your preferences\n\n"
    "You can also just type a question naturally — no command needed."
)

_ABOUT_MESSAGE = (
    "*AI Study Assistant*\n"
    "An AI-powered study companion that answers your questions using your "
    "own course materials, stored in a GitHub repository and indexed for "
    "full-text search. Built with FastAPI, python-telegram-bot, PyMuPDF, "
    "SQLite FTS5, and OpenRouter."
)


def _services(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.application.bot_data


def _is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_user_ids


async def _reject_if_not_admin(update: Update, settings: Settings) -> bool:
    """Return True (and reply with the standard rejection) if the user is not an admin."""
    if _is_admin(update.effective_user.id, settings):
        return False
    await update.message.reply_text(_UNAUTHORIZED_MESSAGE)
    return True


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    user = update.effective_user
    services["repository"].get_or_create_session(user.id, user.username)
    await update.message.reply_text(_WELCOME_MESSAGE, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_HELP_MESSAGE, parse_mode=ParseMode.MARKDOWN)


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_ABOUT_MESSAGE, parse_mode=ParseMode.MARKDOWN)


async def subjects_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository = _services(context)["repository"]
    subjects = repository.list_subjects()
    if not subjects:
        await update.message.reply_text("No subjects are indexed yet. Try /refresh or check back soon.")
        return
    text = "*Available subjects:*\n" + "\n".join(f"- {s}" for s in subjects)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def modules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository = _services(context)["repository"]
    user = update.effective_user
    session = repository.get_or_create_session(user.id, user.username)
    modules = repository.list_modules(session.current_subject)
    if not modules:
        scope = f" for *{session.current_subject}*" if session.current_subject else ""
        await update.message.reply_text(f"No modules found{scope}. Try asking about a topic first.", parse_mode=ParseMode.MARKDOWN)
        return
    scope = f" for *{session.current_subject}*" if session.current_subject else ""
    text = f"*Modules{scope}:*\n" + "\n".join(f"- {m}" for m in modules)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository = _services(context)["repository"]
    user = update.effective_user
    entries = repository.get_recent_history(user.id, limit=10)
    if not entries:
        await update.message.reply_text("You don't have any conversation history yet.")
        return
    lines = ["*Your recent questions:*"]
    for entry in entries[-10:]:
        lines.append(f"- {entry.user_question[:80]}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository = _services(context)["repository"]
    user = update.effective_user
    removed = repository.clear_history(user.id)
    await update.message.reply_text(f"Cleared {removed} conversation entries. Starting fresh!")


async def refresh_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """General-access sync trigger (kept for backward compatibility — see the
    admin-only /sync command for the fuller synchronization summary)."""
    indexing_service = _services(context)["indexing_service"]
    cache = _services(context)["cache"]
    await update.message.reply_text("Syncing study materials from GitHub, this may take a moment...")
    try:
        summary = indexing_service.sync_and_index(force=False)
        cache.clear()
        if summary["status"] == "unchanged":
            await update.message.reply_text("Study materials are already up to date.")
        else:
            await update.message.reply_text(
                f"Sync complete: {summary['indexed']} document(s) indexed, "
                f"{summary['deleted']} removed, {summary['failed']} failed."
            )
    except StudyAssistantError as exc:
        await update.message.reply_text(exc.user_message)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository = _services(context)["repository"]
    user = update.effective_user
    session = repository.get_or_create_session(user.id, user.username)
    text = (
        "*Your settings:*\n"
        f"- Current subject: {session.current_subject or 'not set'}\n"
        f"- Current module: {session.current_module or 'not set'}\n"
        f"- Answer detail level: {session.answer_detail_level}\n\n"
        "Ask about a specific subject/module to change your focus automatically."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ----------------------------------------------------------------------
# Admin-only commands (restricted to ADMIN_USER_IDS — see .env.example)
# ----------------------------------------------------------------------

async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    settings = services["settings"]
    if await _reject_if_not_admin(update, settings):
        return

    indexing_service = services["indexing_service"]
    cache = services["cache"]
    await update.message.reply_text("Synchronizing repository, please wait...")

    try:
        summary = indexing_service.sync_and_index(force=False)
    except StudyAssistantError as exc:
        await update.message.reply_text(f"Synchronization failed: {exc.user_message}")
        return

    cache.clear()
    repository = services["repository"]

    if summary["status"] == "unchanged":
        await update.message.reply_text("Repository already up-to-date. No changes to synchronize.")
        return

    text = (
        "Repository synchronized successfully.\n"
        f"New PDFs: {summary['added']}\n"
        f"Updated PDFs: {summary['updated']}\n"
        f"Deleted PDFs: {summary['deleted']}\n"
        f"Indexed Documents: {repository.document_count()}\n"
        "Synchronization completed successfully."
    )
    if summary["failed"]:
        text += f"\n({summary['failed']} file(s) failed to index — see logs for details.)"
    await update.message.reply_text(text)


async def syncstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    settings = services["settings"]
    if await _reject_if_not_admin(update, settings):
        return

    repository = services["repository"]
    last_run = repository.get_latest_sync_run()
    last_commit = repository.get_last_commit_hash()

    if last_run and last_run.finished_at:
        try:
            from datetime import datetime

            duration = (
                datetime.fromisoformat(last_run.finished_at) - datetime.fromisoformat(last_run.started_at)
            ).total_seconds()
            duration_text = f"{duration:.1f}s"
        except ValueError:
            duration_text = "unknown"
    else:
        duration_text = "unknown"

    text = (
        "*Sync Status*\n"
        f"Last Sync : {last_run.finished_at if last_run else 'never'}\n"
        f"Repository Version : {(last_commit or 'none')[:12]}\n"
        f"Indexed PDFs : {repository.document_count()}\n"
        f"Indexed Pages : {repository.get_indexed_page_total()}\n"
        f"Database Status : {'OK' if last_run is None or last_run.status != 'failed' else 'ERROR'}\n"
        f"Last Sync Duration : {duration_text}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def reindex_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    settings = services["settings"]
    if await _reject_if_not_admin(update, settings):
        return

    indexing_service = services["indexing_service"]
    cache = services["cache"]
    await update.message.reply_text(
        "Rebuilding the search index from locally synced files (no re-download)..."
    )
    try:
        summary = indexing_service.reindex_local()
    except StudyAssistantError as exc:
        await update.message.reply_text(f"Reindex failed: {exc.user_message}")
        return

    cache.clear()
    text = (
        "Reindex complete.\n"
        f"Files found: {summary['total_files']}\n"
        f"Indexed: {summary['indexed']}\n"
        f"Failed: {summary['failed']}\n"
        f"Duration: {summary['duration_seconds']}s"
    )
    await update.message.reply_text(text)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Full system status. Restricted to admins — see /subjects or /settings
    for information regular users can see about the indexed content."""
    services = _services(context)
    settings = services["settings"]
    if await _reject_if_not_admin(update, settings):
        return

    db = services["db"]
    repository = services["repository"]
    report = run_health_check(settings, db, repository)
    component_lines = "\n".join(f"- {c['name']}: {'OK' if c['healthy'] else 'FAIL'} ({c['detail']})" for c in report["components"])

    text = (
        "*System Status*\n"
        f"Application Status : {report['status']}\n"
        f"Indexed Documents : {repository.document_count()}\n"
        f"Current Model : {settings.default_llm_model}\n"
        f"Version : {report['version']}\n\n"
        f"{component_lines}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle any free-text message as a study question."""
    conversation_manager: ConversationManager = _services(context)["conversation_manager"]
    user = update.effective_user
    message_text = update.message.text

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        result = conversation_manager.handle_message(user.id, user.username, message_text)
    except LLMServiceError as exc:
        await update.message.reply_text(exc.user_message)
        return
    except StudyAssistantError as exc:
        logger.error("Unhandled StudyAssistantError while answering a message: %s", exc)
        await update.message.reply_text(exc.user_message)
        return

    chunks = format_response(result.answer_text, result.primary_reference, result.additional_reference)
    for chunk in chunks:
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception:
            # Fall back to plain text if MarkdownV2 escaping still trips the parser
            # on unusual model output — reliability matters more than styling.
            plain_chunks = format_response(
                result.answer_text, result.primary_reference, result.additional_reference, escape=False
            )
            for plain_chunk in plain_chunks:
                await update.message.reply_text(plain_chunk)
            break
