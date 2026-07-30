"""Shared pytest fixtures for the AI Study Assistant test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("GITHUB_REPO_URL", "https://github.com/test-owner/test-repo")

from app.database.connection import Database  # noqa: E402
from app.database.repository import Repository  # noqa: E402
from app.database.schema import initialize_schema  # noqa: E402
from app.search.search_engine import SearchEngine  # noqa: E402


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Database:
    db = Database(str(tmp_path / "test.db"))
    initialize_schema(db)
    return db


@pytest.fixture()
def repository(tmp_db: Database) -> Repository:
    return Repository(tmp_db)


@pytest.fixture()
def search_engine(tmp_db: Database) -> SearchEngine:
    return SearchEngine(tmp_db, result_limit=5, context_char_limit=4000)


@pytest.fixture()
def sample_document(repository: Repository) -> int:
    doc_id = repository.upsert_document(
        relative_path="OS/Module1/deadlock.pdf",
        file_name="deadlock.pdf",
        subject="OS",
        module="Module1",
        file_hash="abc123",
        repo_version="commit1",
        page_count=2,
    )
    repository.replace_sections(
        doc_id,
        [
            {
                "page_number": 1,
                "heading": "Deadlock",
                "content": (
                    "Deadlock is a situation in operating systems where a set of processes "
                    "are blocked because each process is holding a resource and waiting for "
                    "another resource acquired by some other process."
                ),
            },
            {
                "page_number": 2,
                "heading": "Deadlock Prevention",
                "content": (
                    "Deadlock prevention ensures that at least one of the necessary "
                    "conditions for deadlock cannot hold, such as mutual exclusion, "
                    "hold and wait, no preemption, or circular wait."
                ),
            },
        ],
    )
    return doc_id


@pytest.fixture()
def second_document(repository: Repository) -> int:
    """A second document that only briefly/incidentally mentions the same
    keyword, used to test primary-source selection (the sample_document
    fixture above should consistently outrank this one for "deadlock"
    queries since it's far more focused on the topic).
    """
    doc_id = repository.upsert_document(
        relative_path="OS/Module3/scheduling.pdf",
        file_name="scheduling.pdf",
        subject="OS",
        module="Module3",
        file_hash="def456",
        repo_version="commit1",
        page_count=1,
    )
    repository.replace_sections(
        doc_id,
        [
            {
                "page_number": 5,
                "heading": "Scheduling Overview",
                "content": (
                    "CPU scheduling algorithms determine which process runs next. A poorly "
                    "designed scheduler can occasionally contribute to deadlock in edge cases, "
                    "but this document is primarily about scheduling algorithms like FCFS, SJF, "
                    "and round robin."
                ),
            },
        ],
    )
    return doc_id
