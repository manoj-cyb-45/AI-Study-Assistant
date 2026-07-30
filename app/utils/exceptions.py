"""Centralized exception hierarchy for the AI Study Assistant.

Every service in the application raises one of these exceptions instead of
letting raw third-party exceptions bubble up. This keeps error handling
consistent and ensures internal details never leak to end users.
"""

from __future__ import annotations


class StudyAssistantError(Exception):
    """Base class for all application-specific errors."""

    def __init__(self, message: str, *, user_message: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        # Safe, user-facing message. Falls back to a generic message so we
        # never leak internals if a caller forgets to set one.
        self.user_message = user_message or "Something went wrong. Please try again."


class ConfigurationError(StudyAssistantError):
    """Raised when required configuration is missing or invalid."""


class DatabaseError(StudyAssistantError):
    """Raised when a SQLite operation fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message, user_message="A database error occurred. Please try again shortly.")


class GitHubSyncError(StudyAssistantError):
    """Raised when GitHub repository synchronization fails."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            user_message="Study material sync is temporarily unavailable. Existing content is still usable.",
        )


class PDFProcessingError(StudyAssistantError):
    """Raised when a single PDF file cannot be processed.

    Callers should catch this per-file and continue processing the rest of
    the batch rather than aborting the whole sync.
    """

    def __init__(self, message: str, *, file_path: str | None = None) -> None:
        super().__init__(message, user_message="One of the study documents could not be processed.")
        self.file_path = file_path


class SearchError(StudyAssistantError):
    """Raised when the full-text search index cannot be queried."""

    def __init__(self, message: str) -> None:
        super().__init__(message, user_message="Search is temporarily unavailable. Please try again.")


class LLMServiceError(StudyAssistantError):
    """Raised when the language model provider fails or times out."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            user_message="The AI service is temporarily unavailable. Please try again in a moment.",
        )


class LLMRateLimitError(LLMServiceError):
    """Raised specifically when the provider returns a rate-limit response."""

    def __init__(self, message: str) -> None:
        StudyAssistantError.__init__(
            self,
            message,
            user_message="The AI service is busy right now. Please try again in a minute.",
        )


class TelegramDeliveryError(StudyAssistantError):
    """Raised when a message cannot be delivered through the Telegram API."""

    def __init__(self, message: str) -> None:
        super().__init__(message, user_message="Could not send the message. Please try again.")


class SessionError(StudyAssistantError):
    """Raised for conversation/session management failures."""
