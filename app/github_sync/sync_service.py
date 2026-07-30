"""GitHub synchronization service.

The GitHub repository is the single source of truth for study materials.
This service compares the latest commit's file tree against what is already
indexed (tracked by each document's GitHub blob SHA, stored as ``file_hash``)
and downloads only files that are new or changed. Files removed from the
repository are removed locally and from the index.

Repository layout: the top-level folder is always the subject. The module
can come either from a middle folder (``Subject/Module/file.pdf``) or, in a
flat layout (``Subject/file.pdf``), from the filename itself — e.g.
``Module 1.pdf`` or ``Question Bank.pdf``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.database.repository import Repository
from app.utils.exceptions import GitHubSyncError
from app.utils.logger import get_logger

logger = get_logger(__name__)

_GITHUB_API_BASE = "https://api.github.com"
_MODULE_FROM_FILENAME_RE = re.compile(r"(module\s*-?\s*\d+)", re.IGNORECASE)


@dataclass
class SyncResult:
    commit_hash: str
    added: list[str]
    updated: list[str]
    deleted: list[str]
    failed: list[tuple[str, str]]  # (path, error message)
    unchanged: bool = False
    # relative_path -> GitHub blob SHA, for files in added/updated. This SHA
    # is what gets stored as each document's file_hash for change detection
    # on the next sync — it must stay consistent with what
    # list_all_document_paths() compares against.
    blob_shas: dict[str, str] | None = None


def _parse_owner_repo(repo_url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub URL like https://github.com/owner/repo."""
    parsed = urlparse(repo_url.rstrip("/"))
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise GitHubSyncError(f"Could not parse owner/repo from GITHUB_REPO_URL: {repo_url}")
    owner, repo = parts[0], parts[1]
    repo = re.sub(r"\.git$", "", repo)
    return owner, repo


class GitHubSyncService:
    """Synchronizes PDF study materials from a GitHub repository to disk."""

    def __init__(
        self,
        repository: Repository,
        *,
        repo_url: str,
        branch: str,
        github_token: str,
        upload_dir: str,
        request_timeout_seconds: int = 30,
    ) -> None:
        self.repository = repository
        self.owner, self.repo = _parse_owner_repo(repo_url)
        self.branch = branch
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = request_timeout_seconds

        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"
        self._client = httpx.Client(base_url=_GITHUB_API_BASE, headers=headers, timeout=self.timeout)

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_latest_commit_hash(self) -> str:
        try:
            response = self._client.get(f"/repos/{self.owner}/{self.repo}/commits/{self.branch}")
            response.raise_for_status()
            return response.json()["sha"]
        except httpx.HTTPStatusError as exc:
            raise GitHubSyncError(
                f"GitHub API returned {exc.response.status_code} fetching latest commit: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise GitHubSyncError(f"Network error contacting GitHub: {exc}") from exc

    def sync(self, *, force: bool = False) -> SyncResult:
        """Synchronize local study materials with the configured repository.

        Args:
            force: If True, re-download and reprocess every file even if the
                commit hash hasn't changed. Used by the /refresh command and
                the rebuild_database utility script.
        """
        latest_commit = self.get_latest_commit_hash()
        last_commit = self.repository.get_last_commit_hash()

        if not force and latest_commit == last_commit:
            logger.info("GitHub repository unchanged (commit %s); skipping sync.", latest_commit[:8])
            return SyncResult(commit_hash=latest_commit, added=[], updated=[], deleted=[], failed=[], unchanged=True)

        run_id = self.repository.start_sync_run(latest_commit)
        added: list[str] = []
        updated: list[str] = []
        deleted: list[str] = []
        failed: list[tuple[str, str]] = []
        blob_shas: dict[str, str] = {}

        try:
            remote_files = self._list_remote_pdf_files(latest_commit)
            local_files = self.repository.list_all_document_paths()

            remote_paths = {f["path"] for f in remote_files}
            for path in set(local_files) - remote_paths:
                self._delete_local_file(path)
                deleted.append(path)

            for file_info in remote_files:
                path = file_info["path"]
                blob_sha = file_info["sha"]
                if not force and local_files.get(path) == blob_sha:
                    continue  # unchanged
                try:
                    self._download_file(file_info)
                    blob_shas[path] = blob_sha
                    if path in local_files:
                        updated.append(path)
                    else:
                        added.append(path)
                except (httpx.HTTPError, OSError) as exc:
                    logger.error("Failed to download %s: %s", path, exc)
                    failed.append((path, str(exc)))

            self.repository.set_last_commit_hash(latest_commit)
            self.repository.finish_sync_run(
                run_id,
                files_added=len(added),
                files_updated=len(updated),
                files_deleted=len(deleted),
                files_failed=len(failed),
                status="completed" if not failed else "completed_with_errors",
            )
            logger.info(
                "GitHub sync complete: +%d ~%d -%d (failed=%d)",
                len(added), len(updated), len(deleted), len(failed),
            )
            return SyncResult(latest_commit, added, updated, deleted, failed, blob_shas=blob_shas)

        except GitHubSyncError as exc:
            self.repository.finish_sync_run(
                run_id,
                files_added=len(added),
                files_updated=len(updated),
                files_deleted=len(deleted),
                files_failed=len(failed),
                status="failed",
                error_message=str(exc),
            )
            logger.error("GitHub sync failed, continuing with previously synced content: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _list_remote_pdf_files(self, commit_sha: str) -> list[dict]:
        try:
            response = self._client.get(
                f"/repos/{self.owner}/{self.repo}/git/trees/{commit_sha}",
                params={"recursive": "1"},
            )
            response.raise_for_status()
            tree = response.json()
        except httpx.HTTPStatusError as exc:
            raise GitHubSyncError(f"GitHub API error listing repo tree: {exc}") from exc
        except httpx.HTTPError as exc:
            raise GitHubSyncError(f"Network error listing repo tree: {exc}") from exc

        if tree.get("truncated"):
            logger.warning(
                "GitHub tree response was truncated by the API; some files may be missed. "
                "Consider organizing the repository into fewer, smaller subdirectories."
            )

        return [
            {"path": item["path"], "sha": item["sha"], "size": item.get("size", 0)}
            for item in tree.get("tree", [])
            if item["type"] == "blob" and item["path"].lower().endswith(".pdf")
        ]

    def _download_file(self, file_info: dict) -> None:
        path = file_info["path"]
        blob_sha = file_info["sha"]
        response = self._client.get(f"/repos/{self.owner}/{self.repo}/git/blobs/{blob_sha}")
        response.raise_for_status()
        blob = response.json()

        import base64

        if blob.get("encoding") != "base64":
            raise GitHubSyncError(f"Unexpected blob encoding for {path}: {blob.get('encoding')}")

        content = base64.b64decode(blob["content"])
        local_path = self.upload_dir / path
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content)

    def _delete_local_file(self, relative_path: str) -> None:
        local_path = self.upload_dir / relative_path
        if local_path.exists():
            local_path.unlink()
        self.repository.mark_document_deleted(relative_path)

    @staticmethod
    def classify_subject_module(relative_path: str) -> tuple[str | None, str | None]:
        """Derive (subject, module) from the repository path.

        Subject is always the top-level folder name. Module resolution
        supports two repository layouts:

        - Nested: ``Subject/Module/file.pdf`` — module is the middle folder.
        - Flat:   ``Subject/file.pdf``, where the filename itself encodes
          the module, e.g. ``Module 1.pdf`` -> "Module 1",
          ``Question Bank.pdf`` -> "Question Bank".
        """
        path = Path(relative_path)
        parts = path.parts
        subject = parts[0] if len(parts) >= 1 else None

        if len(parts) >= 3:
            return subject, parts[1]

        filename_stem = path.stem.strip()
        match = _MODULE_FROM_FILENAME_RE.search(filename_stem)
        if match:
            digits = re.search(r"\d+", match.group(1))
            module = f"Module {digits.group(0)}" if digits else match.group(1).title()
        else:
            module = filename_stem or None

        return subject, module
