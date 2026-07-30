"""Tests for admin authorization in app.telegram_bot.handlers.

Uses lightweight stand-ins for python-telegram-bot's Update/Context objects
instead of spinning up a real Application — handlers only touch
update.effective_user, update.message.reply_text, and
context.application.bot_data, so a minimal fake is enough and keeps these
tests fast and dependency-free.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config.settings import Settings
from app.telegram_bot import handlers


def _make_settings(admin_ids: tuple[int, ...]) -> Settings:
    return Settings(
        telegram_bot_token="t", telegram_webhook_url="", telegram_webhook_secret="", telegram_use_webhook=False,
        openrouter_api_key="t", openrouter_base_url="https://openrouter.ai/api/v1", default_llm_model="m",
        llm_temperature=0.4, llm_max_tokens=100, llm_request_timeout_seconds=30, llm_max_retries=1,
        github_repo_url="https://github.com/a/b", github_branch="main", github_token="",
        admin_user_ids=admin_ids, sync_interval_minutes=0,
        database_path="x.db", upload_dir="repo", cache_dir="cache", log_dir="logs", backup_dir="backups",
        max_conversation_history=10, search_result_limit=5, search_context_char_limit=1000,
        session_idle_timeout_minutes=60, request_timeout_seconds=30, log_level="INFO", environment="test",
        api_host="0.0.0.0", api_port=8000,
    )


def _make_update(user_id: int) -> SimpleNamespace:
    message = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), message=message)


def _make_context(bot_data: dict) -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data=bot_data))


@pytest.mark.asyncio
async def test_non_admin_is_rejected_from_sync_command() -> None:
    settings = _make_settings(admin_ids=(111,))
    update = _make_update(user_id=999)
    context = _make_context({"settings": settings})

    await handlers.sync_command(update, context)

    update.message.reply_text.assert_awaited_once_with(handlers._UNAUTHORIZED_MESSAGE)


@pytest.mark.asyncio
async def test_admin_passes_authorization_check_for_status() -> None:
    settings = _make_settings(admin_ids=(111,))
    update = _make_update(user_id=111)
    assert not await handlers._reject_if_not_admin(update, settings)
    update.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_admin_rejected_from_syncstatus_and_reindex() -> None:
    settings = _make_settings(admin_ids=(111,))

    for command in (handlers.syncstatus_command, handlers.reindex_command, handlers.status_command):
        update = _make_update(user_id=42)
        context = _make_context({"settings": settings})
        await command(update, context)
        update.message.reply_text.assert_awaited_once_with(handlers._UNAUTHORIZED_MESSAGE)
