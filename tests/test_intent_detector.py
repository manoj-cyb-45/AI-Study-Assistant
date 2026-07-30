"""Tests for app.search.intent_detector."""

from __future__ import annotations

from app.database.repository import Repository
from app.search.intent_detector import (
    Intent,
    detect_intent,
    detect_requested_marks,
    detect_requested_mcq_count,
    detect_subject_module,
)


def test_detect_intent_mcq() -> None:
    assert detect_intent("Give me 5 MCQs on binary trees") == Intent.MCQ


def test_detect_intent_comparison() -> None:
    assert detect_intent("Compare TCP and UDP") == Intent.COMPARISON


def test_detect_intent_definition() -> None:
    assert detect_intent("What is a deadlock?") == Intent.DEFINITION


def test_detect_intent_follow_up() -> None:
    assert detect_intent("Give an example") == Intent.FOLLOW_UP
    assert detect_intent("What about its disadvantages") == Intent.FOLLOW_UP


def test_detect_intent_programming_question() -> None:
    assert detect_intent("Write a program to reverse a linked list") == Intent.PROGRAMMING_QUESTION


def test_detect_intent_defaults_to_explanation_for_longer_text() -> None:
    assert detect_intent("Tell me how paging works in operating systems") == Intent.EXPLANATION


def test_detect_subject_module_matches_known_subject(repository: Repository, sample_document: int) -> None:
    subject, module = detect_subject_module(
        "Explain deadlock from OS module 1", repository, session_subject=None, session_module=None
    )
    assert subject == "OS"


def test_detect_subject_module_falls_back_to_session(repository: Repository, sample_document: int) -> None:
    subject, _ = detect_subject_module(
        "Give another example", repository, session_subject="OS", session_module="Module1"
    )
    assert subject == "OS"


def test_detect_intent_applications() -> None:
    assert detect_intent("Applications of DBMS") == Intent.APPLICATIONS
    assert detect_intent("Applications") == Intent.APPLICATIONS


def test_detect_intent_features() -> None:
    assert detect_intent("Features of DBMS") == Intent.FEATURES


def test_detect_intent_working() -> None:
    assert detect_intent("Working of a hash table") == Intent.WORKING


def test_detect_intent_advantages_bare_word() -> None:
    assert detect_intent("Advantages") == Intent.ADVANTAGES_DISADVANTAGES


def test_detect_requested_marks() -> None:
    assert detect_requested_marks("Explain DBMS for 10 marks") == 10
    assert detect_requested_marks("Write a 5-mark answer on deadlock") == 5
    assert detect_requested_marks("Explain DBMS") is None


def test_detect_requested_mcq_count_only_applies_to_mcq_intent() -> None:
    assert detect_requested_mcq_count("Give me 25 MCQs on DBMS", Intent.MCQ) == 25
    assert detect_requested_mcq_count("Give me 25 MCQs on DBMS", Intent.EXPLANATION) is None
    assert detect_requested_mcq_count("Explain DBMS", Intent.MCQ) is None


def test_detect_subject_module_matches_via_alias(repository: Repository) -> None:
    repository.upsert_document(
        relative_path="Database Management Systems/Module 1.pdf",
        file_name="Module 1.pdf", subject="Database Management Systems", module="Module 1",
        file_hash="h1", repo_version="c1", page_count=5,
    )
    subject, _ = detect_subject_module(
        "Explain normalization in DBMS", repository, session_subject=None, session_module=None
    )
    assert subject == "Database Management Systems"
