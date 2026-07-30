"""Tests for app.formatting.response_formatter."""

from __future__ import annotations

from app.formatting.response_formatter import format_reference_block, format_response, format_sources_footer


def test_format_reference_block_uses_subject_module_page() -> None:
    block = format_reference_block(
        {"document_name": "Module 1.pdf", "subject": "DBMS", "module": "Module 1", "page_number": 13},
        label="Source",
    )
    assert "Module 1.pdf" not in block
    assert "Subject : DBMS" in block
    assert "Module : Module 1" in block
    assert "Page : 13" in block


def test_format_reference_block_handles_missing_fields() -> None:
    block = format_reference_block({"document_name": "x.pdf", "subject": None, "module": None, "page_number": None})
    assert "Subject : General" in block
    assert "Module : -" in block
    assert "Page : -" in block


def test_format_sources_footer_deduplicates_documents() -> None:
    references = [
        {"document_name": "deadlock.pdf", "page_number": 1},
        {"document_name": "deadlock.pdf", "page_number": 2},
        {"document_name": "semaphores.pdf", "page_number": 5},
    ]
    footer = format_sources_footer(references)
    assert footer.count("deadlock.pdf") == 1
    assert "semaphores.pdf" in footer


def test_format_sources_footer_empty_when_no_references() -> None:
    assert format_sources_footer([]) == ""


def test_format_response_escapes_markdown_special_chars() -> None:
    chunks = format_response("Score: 90% (A+ grade)!")
    assert len(chunks) == 1
    assert "\\!" in chunks[0] or "\\(" in chunks[0]


def test_format_response_preserves_code_blocks() -> None:
    text = "Here is code:\n```python\nprint('hi')\n```\nDone."
    chunks = format_response(text)
    assert "```python" in chunks[0]
    assert "print('hi')" in chunks[0]


def test_format_response_preserves_bold_and_italic_markers() -> None:
    text = "This is *important* and this is _emphasized_ text."
    chunks = format_response(text)
    assert "*important*" in chunks[0]
    assert "_emphasized_" in chunks[0]
    # Stray unmatched punctuation elsewhere still gets escaped.
    assert "\\." in chunks[0]


def test_format_response_converts_headers_to_bold() -> None:
    text = "# Deadlock\n\n## Definition\nDeadlock is a blocking condition."
    chunks = format_response(text)
    assert "*Deadlock*" in chunks[0]
    assert "*Definition*" in chunks[0]
    assert "# " not in chunks[0]


def test_format_response_strips_headers_in_plain_fallback() -> None:
    text = "# Deadlock\n\nSome text."
    chunks = format_response(text, escape=False)
    assert "#" not in chunks[0]
    assert "Deadlock" in chunks[0]


def test_format_response_splits_long_text_into_chunks() -> None:
    long_text = ("A" * 3000 + "\n\n") * 3  # ~9000 chars, well past Telegram's 4096 limit
    chunks = format_response(long_text, escape=False)
    assert len(chunks) > 1
    assert all(len(chunk) <= 4096 for chunk in chunks)


def test_format_response_appends_primary_and_additional_reference() -> None:
    chunks = format_response(
        "Deadlock is a blocking condition.",
        primary_reference={"document_name": "Module 1.pdf", "subject": "OS", "module": "Module 1", "page_number": 18},
        additional_reference={"document_name": "Module 2.pdf", "subject": "OS", "module": "Module 2", "page_number": 4},
        escape=False,
    )
    assert "Source" in chunks[-1]
    assert "Additional Reference" in chunks[-1]
    assert "Subject : OS" in chunks[-1]


def test_format_response_no_footer_when_no_references() -> None:
    chunks = format_response("Plain answer with no sources.", escape=False)
    assert "Source" not in chunks[-1]
