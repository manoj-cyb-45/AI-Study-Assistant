"""OpenRouter language model service.

Wraps all communication with the configured LLM provider behind a small,
provider-agnostic interface (`generate`) so a future provider (Groq, OpenAI,
Anthropic, Gemini, a local Ollama instance, etc.) can be added later by
implementing the same interface, without touching any calling code.

Never logs API keys or full prompt/response bodies — only metadata (model,
token counts, latency, status).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from app.llm.prompt_builder import PromptResult
from app.utils.exceptions import LLMRateLimitError, LLMServiceError
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class LLMResponse:
    text: str
    model: str
    response_time_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None


class OpenRouterService:
    """Chat-completion client for OpenRouter, following the OpenAI-compatible schema."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        default_model: str,
        temperature: float,
        max_tokens: int,
        timeout_seconds: int,
        max_retries: int,
    ) -> None:
        self.default_model = default_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max(1, max_retries)
        self._client = httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                # Required-ish by OpenRouter for attribution; harmless if ignored.
                "HTTP-Referer": "https://github.com/ai-study-assistant",
                "X-Title": "AI Study Assistant",
            },
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def generate(self, prompt: PromptResult, *, model: str | None = None) -> LLMResponse:
        """Generate a chat completion, retrying transient failures.

        Raises:
            LLMRateLimitError: if the provider is rate-limiting requests
                after all retries are exhausted.
            LLMServiceError: for any other unrecoverable failure.
        """
        chosen_model = model or self.default_model
        payload = {
            "model": chosen_model,
            "messages": [{"role": "system", "content": prompt.system_prompt}, *prompt.messages],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            start = time.monotonic()
            try:
                response = self._client.post("/chat/completions", json=payload)
                elapsed_ms = int((time.monotonic() - start) * 1000)

                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", 2 * attempt))
                    logger.warning("OpenRouter rate limited (attempt %d/%d); backing off %.1fs", attempt, self.max_retries, retry_after)
                    if attempt == self.max_retries:
                        raise LLMRateLimitError("OpenRouter rate limit exceeded after retries.")
                    time.sleep(retry_after)
                    continue

                response.raise_for_status()
                data = response.json()
                choice = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})

                logger.info(
                    "LLM request succeeded: model=%s latency_ms=%d prompt_tokens=%s completion_tokens=%s",
                    chosen_model, elapsed_ms, usage.get("prompt_tokens"), usage.get("completion_tokens"),
                )
                return LLMResponse(
                    text=choice,
                    model=chosen_model,
                    response_time_ms=elapsed_ms,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                )

            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning("OpenRouter request timed out (attempt %d/%d)", attempt, self.max_retries)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    last_exc = exc
                    logger.warning(
                        "OpenRouter returned %d (attempt %d/%d), retrying",
                        exc.response.status_code, attempt, self.max_retries,
                    )
                else:
                    raise LLMServiceError(
                        f"OpenRouter returned {exc.response.status_code}: {exc.response.text[:200]}"
                    ) from exc
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning("Network error contacting OpenRouter (attempt %d/%d): %s", attempt, self.max_retries, exc)

            time.sleep(min(2 ** attempt, 10))

        raise LLMServiceError(f"OpenRouter request failed after {self.max_retries} attempts: {last_exc}")
