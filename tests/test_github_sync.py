"""Tests for app.github_sync.sync_service (URL parsing and classification only —
network calls are covered by the end-to-end mocked-transport check done during
manual verification, not unit tests here)."""

from __future__ import annotations

from app.github_sync.sync_service import GitHubSyncService, _parse_owner_repo


def test_parse_owner_repo_variants() -> None:
    assert _parse_owner_repo("https://github.com/owner/repo") == ("owner", "repo")
    assert _parse_owner_repo("https://github.com/owner/repo.git") == ("owner", "repo")
    assert _parse_owner_repo("https://github.com/owner/repo/") == ("owner", "repo")


def test_classify_flat_layout_module_from_filename() -> None:
    assert GitHubSyncService.classify_subject_module("DBMS/Module 1.pdf") == ("DBMS", "Module 1")
    assert GitHubSyncService.classify_subject_module("DBMS/Module 3.pdf") == ("DBMS", "Module 3")


def test_classify_flat_layout_non_numbered_filename_used_as_is() -> None:
    assert GitHubSyncService.classify_subject_module("DBMS/Question Bank.pdf") == ("DBMS", "Question Bank")


def test_classify_flat_layout_subject_with_spaces() -> None:
    assert GitHubSyncService.classify_subject_module("Multimedia Computing/Module 5.pdf") == (
        "Multimedia Computing", "Module 5",
    )


def test_classify_nested_layout_still_supported() -> None:
    assert GitHubSyncService.classify_subject_module("OS/Module1/deadlock.pdf") == ("OS", "Module1")


def test_classify_top_level_file_has_no_module() -> None:
    subject, module = GitHubSyncService.classify_subject_module("deadlock.pdf")
    assert subject == "deadlock.pdf"
    assert module == "deadlock"
