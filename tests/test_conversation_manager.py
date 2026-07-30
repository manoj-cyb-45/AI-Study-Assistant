"""Tests for app.telegram_bot.conversation_manager, using a stub LLM service
so no real network calls are made.
"""

from __future__ import annotations

from app.caching.cache_service import TTLCache
from app.database.repository import Repository
from app.llm.openrouter_service import LLMResponse
from app.search.search_engine import SearchEngine
from app.telegram_bot.conversation_manager import ConversationManager


class StubLLMService:
    def __init__(self, reply_text: str = "Deadlock is a blocking condition.") -> None:
        self.reply_text = reply_text
        self.last_prompt = None

    def generate(self, prompt, model=None):
        self.last_prompt = prompt
        return LLMResponse(
            text=self.reply_text, model="stub-model", response_time_ms=10,
            prompt_tokens=10, completion_tokens=5,
        )


def _manager(repository: Repository, search_engine: SearchEngine, llm: StubLLMService) -> ConversationManager:
    return ConversationManager(repository, search_engine, llm, TTLCache(default_ttl_seconds=60), max_history=10)


def test_handle_message_uses_repository_context(repository: Repository, search_engine: SearchEngine, sample_document: int) -> None:
    llm = StubLLMService()
    manager = _manager(repository, search_engine, llm)

    result = manager.handle_message(111, "student1", "Explain deadlock prevention")

    assert result.used_repository_content is True
    assert "RETRIEVED STUDY MATERIAL" in llm.last_prompt.system_prompt
    assert result.answer_text == "Deadlock is a blocking condition."


def test_handle_message_persists_conversation_history(repository: Repository, search_engine: SearchEngine, sample_document: int) -> None:
    llm = StubLLMService()
    manager = _manager(repository, search_engine, llm)

    manager.handle_message(222, "student2", "What is deadlock?")
    history = repository.get_recent_history(222, limit=10)

    assert len(history) == 1
    assert history[0].user_question == "What is deadlock?"
    assert history[0].llm_model == "stub-model"


def test_handle_message_updates_session_focus(repository: Repository, search_engine: SearchEngine, sample_document: int) -> None:
    llm = StubLLMService()
    manager = _manager(repository, search_engine, llm)

    manager.handle_message(333, "student3", "Explain deadlock from OS")
    session = repository.get_or_create_session(333, "student3")

    assert session.current_subject == "OS"


def test_handle_message_falls_back_when_no_context_found(repository: Repository, search_engine: SearchEngine) -> None:
    llm = StubLLMService(reply_text="I don't have material on that, but generally...")
    manager = _manager(repository, search_engine, llm)

    result = manager.handle_message(444, "student4", "What is quantum entanglement?")

    assert result.used_repository_content is False
    assert "No matching material was found" in llm.last_prompt.system_prompt


def test_follow_up_message_is_anchored_to_previous_topic(repository: Repository, search_engine: SearchEngine, sample_document: int) -> None:
    llm = StubLLMService()
    manager = _manager(repository, search_engine, llm)

    manager.handle_message(555, "student5", "Explain deadlock prevention")
    manager.handle_message(555, "student5", "Give an example")  # follow-up, no "deadlock" keyword

    # The follow-up's search query should have been anchored to the earlier
    # question so it still finds deadlock-related content, not just search
    # for the literal phrase "give an example".
    assert "deadlock" in llm.last_prompt.system_prompt.lower()


def test_mcq_without_material_short_circuits_without_calling_llm(repository: Repository, search_engine: SearchEngine) -> None:
    llm = StubLLMService()
    manager = _manager(repository, search_engine, llm)

    result = manager.handle_message(666, "student6", "Give me 10 MCQs on quantum computing")

    assert result.used_repository_content is False
    assert "couldn't find enough material" in result.answer_text
    assert llm.last_prompt is None  # LLM should never have been called
