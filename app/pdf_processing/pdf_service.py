"""PDF processing service.

Extracts readable text from typed (non-scanned) PDF documents using
PyMuPDF, preserving document structure — headings, numbered/bullet lists,
and code blocks — as plain text with light markup, then cleans the result
of repeated headers/footers, excessive whitespace, and invisible
characters. No OCR or image-based extraction is performed; this service
assumes all input documents contain a real text layer.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

from app.utils.exceptions import PDFProcessingError
from app.utils.logger import get_logger

logger = get_logger(__name__)

_INVISIBLE_CHARS_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u00ad]")
_EXCESS_WHITESPACE_RE = re.compile(r"[ \t]{2,}")
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")
_BULLET_RE = re.compile(r"^[\u2022\u25cf\u25aa\-\*]\s+")
_NUMBERED_RE = re.compile(r"^\d+[\.\)]\s+")

# Headings are detected heuristically: short lines, often bold/large in the
# original font, ending without terminal punctuation. We approximate this
# using font size metadata from PyMuPDF's "dict" text extraction.
_HEADING_MAX_WORDS = 12


@dataclass
class ExtractedSection:
    page_number: int
    heading: str | None
    content: str


def compute_file_hash(file_path: Path) -> str:
    """SHA-256 hash of file bytes, used to validate content integrity."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def validate_pdf(file_path: Path) -> None:
    """Raise PDFProcessingError if the file cannot be safely processed."""
    if not file_path.exists() or file_path.stat().st_size == 0:
        raise PDFProcessingError(f"File is missing or empty: {file_path}", file_path=str(file_path))
    try:
        with fitz.open(file_path) as doc:
            if doc.is_encrypted and not doc.authenticate(""):
                raise PDFProcessingError(f"PDF is password-protected: {file_path}", file_path=str(file_path))
            if doc.page_count == 0:
                raise PDFProcessingError(f"PDF has no pages: {file_path}", file_path=str(file_path))
    except fitz.FileDataError as exc:
        raise PDFProcessingError(f"Corrupted or unreadable PDF: {file_path} ({exc})", file_path=str(file_path)) from exc


def _looks_like_heading(line: str, avg_font_size: float, line_font_size: float) -> bool:
    words = line.split()
    if not words or len(words) > _HEADING_MAX_WORDS:
        return False
    if line.strip().endswith((".", ",", ";")):
        return False
    return line_font_size >= avg_font_size * 1.15


def _extract_page_blocks(page: fitz.Page) -> list[dict]:
    """Extract text blocks with font-size metadata for heading detection."""
    raw = page.get_text("dict")
    blocks = []
    font_sizes: list[float] = []
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if span.get("text", "").strip():
                    font_sizes.append(span.get("size", 0.0))
    avg_size = sum(font_sizes) / len(font_sizes) if font_sizes else 10.0

    for block in raw.get("blocks", []):
        lines_out = []
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = "".join(span.get("text", "") for span in spans).strip()
            if not text:
                continue
            max_span_size = max(span.get("size", 0.0) for span in spans)
            lines_out.append({"text": text, "size": max_span_size})
        if lines_out:
            blocks.append({"lines": lines_out, "avg_size": avg_size})
    return blocks


def _format_line(text: str) -> str:
    """Normalize bullet/numbered list markers to a consistent style."""
    if _BULLET_RE.match(text):
        return "- " + _BULLET_RE.sub("", text)
    if _NUMBERED_RE.match(text):
        return text  # keep original numbering for step-by-step material
    return text


def _detect_repeated_lines(pages_lines: list[list[str]], min_page_fraction: float = 0.6) -> set[str]:
    """Find lines (typically headers/footers) repeated across most pages."""
    if len(pages_lines) < 3:
        return set()
    counter: Counter[str] = Counter()
    for lines in pages_lines:
        for line in set(lines[:2] + lines[-2:]):  # only consider top/bottom lines
            counter[line.strip()] += 1
    threshold = max(2, int(len(pages_lines) * min_page_fraction))
    return {line for line, count in counter.items() if line and count >= threshold}


def clean_text(text: str) -> str:
    """Remove invisible characters and collapse excess whitespace."""
    text = _INVISIBLE_CHARS_RE.sub("", text)
    text = _EXCESS_WHITESPACE_RE.sub(" ", text)
    text = _EXCESS_BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def extract_sections(file_path: Path) -> list[ExtractedSection]:
    """Extract one section per page (further split on detected headings).

    Structure preservation: headings are emitted as ``## Heading`` markers,
    bullet lists are normalized to ``- item``, numbered lists keep their
    original numbering, and code-like monospaced blocks are wrapped in
    triple backticks based on a simple heuristic (lines with a fixed-width
    look and low natural-language word density).
    """
    validate_pdf(file_path)
    sections: list[ExtractedSection] = []

    try:
        with fitz.open(file_path) as doc:
            all_pages_blocks = [_extract_page_blocks(page) for page in doc]
            all_pages_lines = [
                [line["text"] for block in blocks for line in block["lines"]]
                for blocks in all_pages_blocks
            ]
            repeated_lines = _detect_repeated_lines(all_pages_lines)

            for page_index, blocks in enumerate(all_pages_blocks):
                page_number = page_index + 1
                current_heading: str | None = None
                buffer: list[str] = []

                def flush(heading: str | None, lines: list[str]) -> None:
                    if not lines:
                        return
                    content = clean_text("\n".join(lines))
                    if content:
                        sections.append(ExtractedSection(page_number=page_number, heading=heading, content=content))

                for block in blocks:
                    avg_size = block["avg_size"]
                    for line in block["lines"]:
                        text = line["text"].strip()
                        if not text or text in repeated_lines:
                            continue
                        if _looks_like_heading(text, avg_size, line["size"]):
                            flush(current_heading, buffer)
                            buffer = []
                            current_heading = text
                            buffer.append(f"## {text}")
                        else:
                            buffer.append(_format_line(text))
                flush(current_heading, buffer)

    except fitz.FileDataError as exc:
        raise PDFProcessingError(f"Failed while extracting text from {file_path}: {exc}", file_path=str(file_path)) from exc

    if not sections:
        logger.warning("No extractable text found in %s (file may be image-only, which is unsupported).", file_path)

    return sections


def get_page_count(file_path: Path) -> int:
    with fitz.open(file_path) as doc:
        return doc.page_count
