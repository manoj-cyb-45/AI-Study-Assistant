"""Tests for app.pdf_processing.pdf_service.

Uses PyMuPDF itself to generate small real PDF files so extraction logic is
tested against genuine PDF structure rather than mocks.
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from app.pdf_processing.pdf_service import (
    clean_text,
    compute_file_hash,
    extract_sections,
    get_page_count,
    validate_pdf,
)
from app.utils.exceptions import PDFProcessingError


def _make_pdf(path: Path, pages_text: list[str]) -> None:
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
    doc.save(path)
    doc.close()


def test_validate_pdf_accepts_valid_file(tmp_path: Path) -> None:
    pdf_path = tmp_path / "valid.pdf"
    _make_pdf(pdf_path, ["Hello world"])
    validate_pdf(pdf_path)  # should not raise


def test_validate_pdf_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PDFProcessingError):
        validate_pdf(tmp_path / "missing.pdf")


def test_validate_pdf_rejects_empty_file(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty.pdf"
    empty_path.write_bytes(b"")
    with pytest.raises(PDFProcessingError):
        validate_pdf(empty_path)


def test_validate_pdf_rejects_corrupted_file(tmp_path: Path) -> None:
    corrupted_path = tmp_path / "corrupted.pdf"
    corrupted_path.write_bytes(b"%PDF-1.4 this is not a real pdf structure")
    with pytest.raises(PDFProcessingError):
        validate_pdf(corrupted_path)


def test_extract_sections_returns_text_per_page(tmp_path: Path) -> None:
    pdf_path = tmp_path / "two_pages.pdf"
    _make_pdf(pdf_path, ["First page content about deadlocks.", "Second page content about semaphores."])

    sections = extract_sections(pdf_path)
    assert len(sections) >= 2
    page_numbers = {s.page_number for s in sections}
    assert page_numbers == {1, 2}
    combined = " ".join(s.content for s in sections)
    assert "deadlocks" in combined
    assert "semaphores" in combined


def test_get_page_count(tmp_path: Path) -> None:
    pdf_path = tmp_path / "three_pages.pdf"
    _make_pdf(pdf_path, ["A", "B", "C"])
    assert get_page_count(pdf_path) == 3


def test_compute_file_hash_is_deterministic(tmp_path: Path) -> None:
    pdf_path = tmp_path / "hashme.pdf"
    _make_pdf(pdf_path, ["Consistent content"])
    hash1 = compute_file_hash(pdf_path)
    hash2 = compute_file_hash(pdf_path)
    assert hash1 == hash2
    assert len(hash1) == 64  # sha256 hex digest length


def test_clean_text_collapses_whitespace_and_strips_invisible_chars() -> None:
    dirty = "Hello\u200b   world\n\n\n\nAgain"
    cleaned = clean_text(dirty)
    assert "\u200b" not in cleaned
    assert "   " not in cleaned
    assert "\n\n\n" not in cleaned
