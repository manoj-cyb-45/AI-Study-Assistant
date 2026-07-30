"""Tests for app.llm.openrouter_service, using httpx.MockTransport (no live network calls)."""

from __future__ import annotations

import httpx
import pytest

import app.llm.openrouter_service as openrouter_module
from app.llm.openrouter_service import OpenRouterService
from app.llm.prompt_builder import PromptResult
from app.utils.exceptions import LLMRateLimitError, LLMServiceError


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip real backoff delays so retry tests run instantly."""
    monkeypatch.setattr(openrouter_module.time, "sleep", lambda _seconds: None)


def _make_service(transport: httpx.MockTransport) -> OpenRouterService:
    service = OpenRouterService(
        api_key="test-key",
        base_url="https://openrouter.test/api/v1",
        default_model="test-model",
        temperature=0.4,
        max_tokens=100,
        timeout_seconds=5,
        max_retries=2,
    )
    service._client = httpx.Client(
        base_url="https://openrouter.test/api/v1",
        transport=transport,
        headers=service._client.headers,
    )
    return service


def _sample_prompt() -> PromptResult:
    return PromptResult(system_prompt="You are a helpful assistant.", messages=[{"role": "user", "content": "Hi"}])


def test_generate_returns_text_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Deadlock is a blocking condition."}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20},
            },
        )

    service = _make_service(httpx.MockTransport(handler))
    response = service.generate(_sample_prompt())
    assert response.text == "Deadlock is a blocking condition."
    assert response.prompt_tokens == 50


def test_generate_retries_on_server_error_then_succeeds() -> None:
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(500, json={"error": "internal"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}], "usage": {}})

    service = _make_service(httpx.MockTransport(handler))
    response = service.generate(_sample_prompt())
    assert response.text == "OK"
    assert call_count["n"] == 2


def test_generate_raises_rate_limit_error_after_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"})

    service = _make_service(httpx.MockTransport(handler))
    with pytest.raises(LLMRateLimitError):
        service.generate(_sample_prompt())


def test_generate_raises_llm_service_error_on_client_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    service = _make_service(httpx.MockTransport(handler))
    with pytest.raises(LLMServiceError):
        service.generate(_sample_prompt())
