"""Tests for app.config.settings."""

from __future__ import annotations

import os

import pytest

from app.config.settings import get_settings
from app.utils.exceptions import ConfigurationError


def _clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("OPENROUTER_API_KEY", "def")
    monkeypatch.setenv("GITHUB_REPO_URL", "https://github.com/owner/repo")
    _clear_settings_cache()

    settings = get_settings()
    assert settings.telegram_bot_token == "abc"
    assert settings.openrouter_api_key == "def"
    assert settings.github_repo_url == "https://github.com/owner/repo"
    _clear_settings_cache()


def test_settings_raises_when_mandatory_value_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_REPO_URL", raising=False)
    _clear_settings_cache()

    with pytest.raises(ConfigurationError):
        get_settings()
    _clear_settings_cache()


def test_settings_rejects_invalid_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("OPENROUTER_API_KEY", "def")
    monkeypatch.setenv("GITHUB_REPO_URL", "https://github.com/owner/repo")
    monkeypatch.setenv("LLM_TEMPERATURE", "5.0")
    _clear_settings_cache()

    with pytest.raises(ConfigurationError):
        get_settings()
    _clear_settings_cache()
