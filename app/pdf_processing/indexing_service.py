"""Indexing orchestration service.

Ties together GitHub synchronization and PDF processing: for every added or
updated file reported by the sync service, extracts text with PyMuPDF and
writes it into the database (documents + sections, which cascades into the
FTS5 index via triggers). Corrupted or unreadable files are skipped so a
single bad PDF never blocks the rest of the batch or the application.
"""

from __future__ import annotations

import time
from pathlib import Path

from app.database.repository import Repository
from app.github_sync.sync_service import GitHubSyncService, SyncResult
from app.pdf_processing.pdf_service import compute_file_hash, extract_sections, get_page_count
from app.utils.exceptions import PDFProcessingError
from app.utils.logger import get_logger

logger = get_logger(__name__)


class IndexingService:
    """Coordinates repository sync and PDF indexing into SQLite FTS5."""

    def __init__(self, repository: Repository, sync_service: GitHubSyncService, upload_dir: str) -> None:
        self.repository = repository
        self.sync_service = sync_service
        self.upload_dir = Path(upload_dir)

    def sync_and_index(self, *, force: bool = False) -> dict:
        """Run a full sync + index cycle. Returns a summary dict for logging/health."""
        start = time.monotonic()
        result = self.sync_service.sync(force=force)
        if result.unchanged:
            logger.info("Repository already up-to-date.")
            return {
                "status": "unchanged",
                "commit_hash": result.commit_hash,
                "added": 0,
                "updated": 0,
                "indexed": 0,
                "failed": 0,
                "deleted": 0,
                "duration_seconds": round(time.monotonic() - start, 2),
            }

        indexed = 0
        added_count = 0
        updated_count = 0
        failed: list[tuple[str, str]] = list(result.failed)

        blob_shas = result.blob_shas or {}
        added_set = set(result.added)
        for relative_path in result.added + result.updated:
            try:
                self._index_file(
                    relative_path,
                    repo_version=result.commit_hash,
                    file_hash=blob_shas.get(relative_path, result.commit_hash),
                )
                indexed += 1
                if relative_path in added_set:
                    added_count += 1
                else:
                    updated_count += 1
            except PDFProcessingError as exc:
                logger.error("Skipping unindexable file %s: %s", relative_path, exc.message)
                failed.append((relative_path, exc.message))

        duration = round(time.monotonic() - start, 2)
        logger.info(
            "Indexing complete: commit=%s +%d ~%d -%d indexed=%d failed=%d duration=%.2fs",
            result.commit_hash[:8], added_count, updated_count, len(result.deleted), indexed, len(failed), duration,
        )
        return {
            "status": "completed" if not failed else "completed_with_errors",
            "commit_hash": result.commit_hash,
            "added": added_count,
            "updated": updated_count,
            "indexed": indexed,
            "failed": len(failed),
            "failed_files": failed,
            "deleted": len(result.deleted),
            "duration_seconds": duration,
        }

    def reindex_local(self) -> dict:
        """Rebuild the entire FTS5 index from already-synced local PDFs.

        Does not contact GitHub at all — used by the /reindex admin command
        when the search index is suspected to be stale or corrupted but the
        locally synced repository copy itself is fine.
        """
        start = time.monotonic()
        deleted_count = self.repository.delete_all_documents()

        repo_version = self.repository.get_last_commit_hash() or "local-reindex"
        indexed = 0
        failed: list[tuple[str, str]] = []

        pdf_paths = sorted(self.upload_dir.rglob("*.pdf"))
        for local_path in pdf_paths:
            relative_path = str(local_path.relative_to(self.upload_dir))
            try:
                file_hash = compute_file_hash(local_path)
                self._index_file(relative_path, repo_version=repo_version, file_hash=file_hash)
                indexed += 1
            except PDFProcessingError as exc:
                logger.error("Skipping unindexable file during reindex %s: %s", relative_path, exc.message)
                failed.append((relative_path, exc.message))

        duration = round(time.monotonic() - start, 2)
        logger.info(
            "Local reindex complete: %d documents removed, %d files found, %d indexed, %d failed, duration=%.2fs",
            deleted_count, len(pdf_paths), indexed, len(failed), duration,
        )
        return {
            "status": "completed" if not failed else "completed_with_errors",
            "total_files": len(pdf_paths),
            "indexed": indexed,
            "failed": len(failed),
            "failed_files": failed,
            "duration_seconds": duration,
        }

    def _index_file(self, relative_path: str, *, repo_version: str, file_hash: str) -> None:
        local_path = self.upload_dir / relative_path
        subject, module = GitHubSyncService.classify_subject_module(relative_path)
        sections = extract_sections(local_path)
        page_count = get_page_count(local_path)

        doc_id = self.repository.upsert_document(
            relative_path=relative_path,
            file_name=Path(relative_path).name,
            subject=subject,
            module=module,
            file_hash=file_hash,
            repo_version=repo_version,
            page_count=page_count,
        )
        self.repository.replace_sections(
            doc_id,
            [{"page_number": s.page_number, "heading": s.heading, "content": s.content} for s in sections],
        )
