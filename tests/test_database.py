"""Tests for the database schema and repository layer."""

from __future__ import annotations

from app.database.models import ConversationEntry
from app.database.repository import Repository


def test_upsert_document_creates_new_row(repository: Repository) -> None:
    doc_id = repository.upsert_document(
        relative_path="CS/Module1/algorithms.pdf",
        file_name="algorithms.pdf",
        subject="CS",
        module="Module1",
        file_hash="hash1",
        repo_version="commit1",
        page_count=10,
    )
    assert doc_id > 0
    doc = repository.get_document_by_path("CS/Module1/algorithms.pdf")
    assert doc is not None
    assert doc.subject == "CS"
    assert doc.file_hash == "hash1"


def test_upsert_document_updates_existing_row(repository: Repository) -> None:
    path = "CS/Module1/algorithms.pdf"
    first_id = repository.upsert_document(
        relative_path=path, file_name="algorithms.pdf", subject="CS", module="Module1",
        file_hash="hash1", repo_version="commit1", page_count=10,
    )
    second_id = repository.upsert_document(
        relative_path=path, file_name="algorithms.pdf", subject="CS", module="Module1",
        file_hash="hash2", repo_version="commit2", page_count=12,
    )
    assert first_id == second_id
    doc = repository.get_document_by_path(path)
    assert doc.file_hash == "hash2"
    assert doc.page_count == 12


def test_mark_document_deleted_removes_document_and_sections(repository: Repository, sample_document: int) -> None:
    repository.mark_document_deleted("OS/Module1/deadlock.pdf")
    assert repository.get_document_by_path("OS/Module1/deadlock.pdf") is None


def test_list_subjects_and_modules(repository: Repository, sample_document: int) -> None:
    assert repository.list_subjects() == ["OS"]
    assert repository.list_modules("OS") == ["Module1"]


def test_session_created_with_defaults(repository: Repository) -> None:
    session = repository.get_or_create_session(12345, "student1")
    assert session.telegram_user_id == 12345
    assert session.current_subject is None
    assert session.answer_detail_level == "standard"


def test_update_session_focus(repository: Repository) -> None:
    repository.get_or_create_session(12345, "student1")
    repository.update_session_focus(12345, subject="OS", module="Module1")
    session = repository.get_or_create_session(12345, "student1")
    assert session.current_subject == "OS"
    assert session.current_module == "Module1"


def test_conversation_history_round_trip(repository: Repository) -> None:
    entry = ConversationEntry(
        id=None,
        telegram_user_id=999,
        created_at="2026-01-01T00:00:00+00:00",
        detected_subject="OS",
        detected_module="Module1",
        detected_intent="explanation",
        user_question="What is deadlock?",
        assistant_response="Deadlock is...",
        retrieved_references=None,
        response_time_ms=500,
        llm_model="test-model",
    )
    repository.add_conversation_entry(entry)

    history = repository.get_recent_history(999, limit=10)
    assert len(history) == 1
    assert history[0].user_question == "What is deadlock?"

    removed = repository.clear_history(999)
    assert removed == 1
    assert repository.get_recent_history(999, limit=10) == []


def test_get_indexed_page_total(repository: Repository, sample_document: int) -> None:
    assert repository.get_indexed_page_total() == 2


def test_delete_all_documents(repository: Repository, sample_document: int) -> None:
    removed = repository.delete_all_documents()
    assert removed == 1
    assert repository.document_count() == 0
    assert repository.get_document_by_path("OS/Module1/deadlock.pdf") is None
