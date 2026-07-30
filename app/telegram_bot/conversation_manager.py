"""Conversation manager.

The central orchestrator for a single incoming user message: it loads the
user's session and recent history, detects intent/subject/module/marks,
searches the FTS5 index (anchoring follow-up questions to the last real
topic so "Advantages" after "Explain DBMS" searches for DBMS, not the
word "advantages"), builds the prompt, calls the LLM service, formats the
response, and persists the interaction to conversation history. This keeps
Telegram handlers thin — they only translate between Telegram's API and this
service.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app.caching.cache_service import TTLCache
from app.database.models import ConversationEntry
from app.database.repository import Repository
from app.llm.openrouter_service import OpenRouterService
from app.llm.prompt_builder import build_prompt
from app.search.intent_detector import Intent, build_detected_context
from app.search.search_engine import SearchEngine, SearchFilters
from app.utils.exceptions import LLMServiceError, SearchError
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Shown when a user asks for MCQs but no matching study material was found —
# MCQs must come only from uploaded material, never from general knowledge,
# so we short-circuit before calling the LLM at all.
MCQ_NO_MATERIAL_MESSAGE = (
    "I couldn't find enough material on this topic in your uploaded study material to "
    "generate MCQs from it. MCQs are only generated from your course repository, not general "
    "knowledge — try a different topic, or check /subjects for what's indexed."
)


@dataclass
class TurnResult:
    answer_text: str
    primary_reference: dict | None
    additional_reference: dict | None
    intent: Intent
    subject: str | None
    module: str | None
    used_repository_content: bool


class ConversationManager:
    """Handles one full question-answer turn for a Telegram user."""

    def __init__(
        self,
        repository: Repository,
        search_engine: SearchEngine,
        llm_service: OpenRouterService,
        cache: TTLCache,
        *,
        max_history: int,
    ) -> None:
        self.repository = repository
        self.search_engine = search_engine
        self.llm_service = llm_service
        self.cache = cache
        self.max_history = max_history

    def handle_message(self, telegram_user_id: int, username: str | None, message: str) -> TurnResult:
        session = self.repository.get_or_create_session(telegram_user_id, username)
        history = self.repository.get_recent_history(telegram_user_id, self.max_history)

        detected = build_detected_context(
            message,
            self.repository,
            session_subject=session.current_subject,
            session_module=session.current_module,
        )

        if detected.subject and detected.subject != session.current_subject:
            self.repository.update_session_focus(telegram_user_id, subject=detected.subject)
        if detected.module and detected.module != session.current_module:
            self.repository.update_session_focus(telegram_user_id, module=detected.module)

        search_query = self._resolve_search_query(message, detected.is_follow_up, history)

        filters = SearchFilters(subject=detected.subject, module=detected.module)
        cache_key = f"search:{detected.subject}:{detected.module}:{search_query.strip().lower()}"

        try:
            results = self.cache.get_or_set(
                cache_key,
                lambda: self.search_engine.search(
                    search_query, filters, detected_subject=detected.subject, detected_module=detected.module,
                ),
                ttl_seconds=120,
            )
        except SearchError as exc:
            logger.error("Search failed, falling back to no-context answer: %s", exc)
            results = []

        if not results and (detected.subject or detected.module):
            # Widen the search if a subject/module filter produced nothing.
            try:
                results = self.search_engine.search(
                    search_query, SearchFilters(),
                    detected_subject=detected.subject, detected_module=detected.module,
                )
            except SearchError:
                results = []

        # MCQs must come only from uploaded material — never fall back to
        # general knowledge for them (rule: MCQ generation, uploaded-material-only).
        if detected.intent == Intent.MCQ and not results:
            return TurnResult(
                answer_text=MCQ_NO_MATERIAL_MESSAGE,
                primary_reference=None,
                additional_reference=None,
                intent=detected.intent,
                subject=detected.subject,
                module=detected.module,
                used_repository_content=False,
            )

        context = self.search_engine.build_single_source_context(results) if results else None
        context_text = context.context_text if context else ""
        primary_reference = context.primary_reference if context else None
        additional_reference = context.additional_reference if context else None
        has_context = bool(context and context.used_repository_content)

        prompt = build_prompt(
            user_question=message,
            intent=detected.intent,
            context_text=context_text,
            has_context=has_context,
            history=history,
            subject=detected.subject,
            module=detected.module,
            requested_marks=detected.requested_marks,
            requested_mcq_count=detected.requested_mcq_count,
        )

        start = time.monotonic()
        try:
            llm_response = self.llm_service.generate(prompt)
        except LLMServiceError as exc:
            logger.error("LLM generation failed: %s", exc)
            raise
        elapsed_ms = int((time.monotonic() - start) * 1000)

        stored_references = None
        if primary_reference or additional_reference:
            stored_references = json.dumps({"primary": primary_reference, "additional": additional_reference})

        entry = ConversationEntry(
            id=None,
            telegram_user_id=telegram_user_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            detected_subject=detected.subject,
            detected_module=detected.module,
            detected_intent=detected.intent.value,
            user_question=message,
            assistant_response=llm_response.text,
            retrieved_references=stored_references,
            response_time_ms=elapsed_ms,
            llm_model=llm_response.model,
        )
        self.repository.add_conversation_entry(entry)

        return TurnResult(
            answer_text=llm_response.text,
            primary_reference=primary_reference,
            additional_reference=additional_reference,
            intent=detected.intent,
            subject=detected.subject,
            module=detected.module,
            used_repository_content=has_context,
        )

    @staticmethod
    def _resolve_search_query(message: str, is_follow_up: bool, history: list[ConversationEntry]) -> str:
        """Anchor a follow-up message to the last real topic asked about.

        Without this, a follow-up like "Advantages" (after "Explain DBMS")
        would search FTS5 for the literal word "advantages" instead of
        staying scoped to DBMS — even though session subject/module filters
        narrow the *filter*, the *query text* itself needs the topic too for
        BM25/keyword matching to find the right chunks.
        """
        if not is_follow_up:
            return message
        anchor = next(
            (entry.user_question for entry in reversed(history) if entry.detected_intent != Intent.FOLLOW_UP.value),
            None,
        )
        return f"{anchor} {message}" if anchor else message
