"""FastAPI middleware.

Logs every incoming request with its processing time and converts any
unhandled exception into a standardized JSON error response so internal
details (stack traces, exception messages) never reach API clients. This
middleware only wraps FastAPI's HTTP routes — it does not interfere with
Telegram's own update handling, which runs through python-telegram-bot.
"""

from __future__ import annotations

import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.utils.exceptions import StudyAssistantError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs method, path, status code, and duration for every request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())[:8]
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.exception(
                "Unhandled exception [%s] %s %s (%dms)", request_id, request.method, request.url.path, elapsed_ms
            )
            raise
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "[%s] %s %s -> %d (%dms)", request_id, request.method, request.url.path, response.status_code, elapsed_ms
        )
        response.headers["X-Request-ID"] = request_id
        return response


async def study_assistant_exception_handler(request: Request, exc: StudyAssistantError) -> JSONResponse:
    """Convert application exceptions into a standardized JSON error body."""
    logger.error("Application error handling %s %s: %s", request.method, request.url.path, exc.message)
    return JSONResponse(status_code=502, content={"error": exc.user_message})


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler: never leak stack traces or internal details."""
    logger.exception("Unhandled exception for %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"error": "An unexpected error occurred."})
