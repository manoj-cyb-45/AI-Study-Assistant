"""Tests for app.llm.prompt_builder."""

from __future__ import annotations

from app.database.models import ConversationEntry
from app.llm.prompt_builder import build_prompt
from app.search.intent_detector import Intent


def test_build_prompt_includes_context_when_present() -> None:
    result = build_prompt(
        user_question="What is deadlock?",
        intent=Intent.DEFINITION,
        context_text="Deadlock is a blocking condition...",
        has_context=True,
        history=[],
        subject="OS",
        module="Module1",
    )
    assert "Deadlock is a blocking condition" in result.system_prompt
    assert "OS / Module1" in result.system_prompt
    assert result.messages[-1] == {"role": "user", "content": "What is deadlock?"}


def test_build_prompt_notes_missing_context() -> None:
    result = build_prompt(
        user_question="What is deadlock?",
        intent=Intent.DEFINITION,
        context_text="",
        has_context=False,
        history=[],
        subject=None,
        module=None,
    )
    assert "No matching material was found" in result.system_prompt


def test_build_prompt_includes_intent_specific_instruction() -> None:
    result = build_prompt(
        user_question="Compare TCP and UDP",
        intent=Intent.COMPARISON,
        context_text="",
        has_context=False,
        history=[],
        subject=None,
        module=None,
    )
    assert "table" in result.system_prompt.lower()


def test_build_prompt_includes_conversation_history() -> None:
    history = [
        ConversationEntry(
            id=1, telegram_user_id=1, created_at="now", detected_subject=None, detected_module=None,
            detected_intent="explanation", user_question="Explain deadlock",
            assistant_response="Deadlock happens when...", retrieved_references=None,
            response_time_ms=100, llm_model="test",
        )
    ]
    result = build_prompt(
        user_question="Give an example",
        intent=Intent.FOLLOW_UP,
        context_text="",
        has_context=False,
        history=history,
        subject=None,
        module=None,
    )
    assert {"role": "user", "content": "Explain deadlock"} in result.messages
    assert {"role": "assistant", "content": "Deadlock happens when..."} in result.messages


def test_build_prompt_uses_exact_not_found_notice_text() -> None:
    from app.llm.prompt_builder import NOT_FOUND_NOTICE

    result = build_prompt(
        user_question="What is quantum entanglement?", intent=Intent.EXPLANATION,
        context_text="", has_context=False, history=[], subject=None, module=None,
    )
    assert NOT_FOUND_NOTICE in result.system_prompt
    assert NOT_FOUND_NOTICE == (
        "This topic was not found in your uploaded study material. "
        "The following explanation is based on general knowledge."
    )


def test_build_prompt_includes_marks_instruction() -> None:
    result = build_prompt(
        user_question="Explain deadlock", intent=Intent.EXAM_ANSWER, context_text="ctx", has_context=True,
        history=[], subject=None, module=None, requested_marks=10,
    )
    assert "10-mark" in result.system_prompt


def test_build_prompt_includes_mcq_count_instruction() -> None:
    result = build_prompt(
        user_question="Give me 25 MCQs on DBMS", intent=Intent.MCQ, context_text="ctx", has_context=True,
        history=[], subject=None, module=None, requested_mcq_count=25,
    )
    assert "exactly 25 MCQs" in result.system_prompt


def test_build_prompt_includes_diagram_handling_rule() -> None:
    result = build_prompt(
        user_question="Explain OSI model", intent=Intent.EXPLANATION, context_text="ctx", has_context=True,
        history=[], subject=None, module=None,
    )
    assert "Diagram Reference" in result.system_prompt


def test_build_prompt_programming_intent_uses_new_section_order() -> None:
    result = build_prompt(
        user_question="Write a program to reverse a linked list", intent=Intent.PROGRAMMING_QUESTION,
        context_text="", has_context=False, history=[], subject=None, module=None,
    )
    assert "Constraints" in result.system_prompt
    assert "Python Solution" in result.system_prompt
