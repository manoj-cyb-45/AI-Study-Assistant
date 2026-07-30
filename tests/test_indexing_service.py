"""Tests for app.pdf_processing.indexing_service.IndexingService.reindex_local().

reindex_local() must rebuild the FTS5 index from whatever PDFs already sit
in the upload directory, without touching the network — so these tests
write real PDFs straight to disk instead of going through GitHubSyncService.
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from app.database.connection import Database
from app.database.repository import Repository
from app.database.schema import initialize_schema
from app.pdf_processing.indexing_service import IndexingService


def _write_pdf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    doc.save(path)
    doc.close()


@pytest.fixture()
def local_repo(tmp_path: Path):
    db = Database(str(tmp_path / "test.db"))
    initialize_schema(db)
    repository = Repository(db)
    upload_dir = tmp_path / "repo"
    upload_dir.mkdir()

    class _NoOpSyncService:
        """reindex_local() never calls the sync service — this stands in for it."""

    indexing_service = IndexingService(repository, _NoOpSyncService(), str(upload_dir))
    return repository, indexing_service, upload_dir


def test_reindex_local_indexes_all_local_pdfs(local_repo) -> None:
    repository, indexing_service, upload_dir = local_repo
    _write_pdf(upload_dir / "DBMS" / "Module 1.pdf", "Normalization is a database design technique.")
    _write_pdf(upload_dir / "DBMS" / "Module 2.pdf", "Transactions ensure ACID properties.")

    summary = indexing_service.reindex_local()

    assert summary["total_files"] == 2
    assert summary["indexed"] == 2
    assert summary["failed"] == 0
    assert repository.document_count() == 2
    doc = repository.get_document_by_path("DBMS/Module 1.pdf")
    assert doc.subject == "DBMS"
    assert doc.module == "Module 1"


def test_reindex_local_wipes_stale_documents_first(local_repo) -> None:
    repository, indexing_service, upload_dir = local_repo
    repository.upsert_document(
        relative_path="Stale/Old.pdf", file_name="Old.pdf", subject="Stale", module="Old",
        file_hash="x", repo_version="c1", page_count=1,
    )
    _write_pdf(upload_dir / "DBMS" / "Module 1.pdf", "Normalization content.")

    indexing_service.reindex_local()

    assert repository.get_document_by_path("Stale/Old.pdf") is None
    assert repository.document_count() == 1


def test_reindex_local_skips_corrupted_files_without_failing_the_batch(local_repo) -> None:
    repository, indexing_service, upload_dir = local_repo
    _write_pdf(upload_dir / "DBMS" / "Module 1.pdf", "Good content here.")
    corrupted = upload_dir / "DBMS" / "Module 2.pdf"
    corrupted.parent.mkdir(parents=True, exist_ok=True)
    corrupted.write_bytes(b"not a real pdf")

    summary = indexing_service.reindex_local()

    assert summary["indexed"] == 1
    assert summary["failed"] == 1
    assert repository.document_count() == 1
