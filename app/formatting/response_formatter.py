"""Response formatting for Telegram delivery.

The LLM is instructed to produce lightly-marked-up text (headings, *bold*,
_italic_, fenced code). This module turns that into something Telegram's
MarkdownV2 parser will actually render correctly:

- ATX-style '#'/'##' headings (which MarkdownV2 doesn't support natively)
  are converted to bold section titles.
- '*bold*' and '_italic_' spans are preserved as real formatting instead of
  being escaped into literal asterisks/underscores.
- Every other MarkdownV2-special character is escaped so arbitrary LLM
  prose (periods, parentheses, percent signs, hyphens, etc.) can't break
  the parser.
- Code fences and inline code are left completely untouched.
- Oversized responses are split into multiple messages (Telegram caps
  messages at 4096 characters).
- A single "Source" citation block (and, occasionally, one "Additional
  Reference" block) is appended, built from retrieved metadata rather than
  raw filenames.
"""

from __future__ import annotations

import re

_TELEGRAM_MESSAGE_LIMIT = 4096
# Characters with special meaning in Telegram MarkdownV2 that must be
# escaped when they appear outside of an intentional formatting construct.
_MARKDOWN_V2_SPECIAL = r"_*[]()~`>#+-=|{}.!"

_CODE_SPLIT_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]*`)")
_INLINE_EMPHASIS_RE = re.compile(r"(\*[^\n*]+\*)|(_[^\n_]+_)")
_HEADER_LINE_RE = re.compile(r"^ {0,3}#{1,6}[ \t]+(.*)$", re.MULTILINE)


def _escape_chars(text: str, *, exclude: str = "") -> str:
    special = "".join(c for c in _MARKDOWN_V2_SPECIAL if c not in exclude)
    return re.sub(f"([{re.escape(special)}])", r"\\\1", text)


def _escape_plain_segment(segment: str) -> str:
    """Escape a code-free segment, preserving *bold*/_italic_ spans as real
    formatting (their delimiters stay unescaped; their inner text is still
    escaped for every other special character).
    """
    out: list[str] = []
    pos = 0
    for match in _INLINE_EMPHASIS_RE.finditer(segment):
        out.append(_escape_chars(segment[pos:match.start()]))
        if match.group(1):  # *bold*
            inner = match.group(1)[1:-1]
            out.append("*" + _escape_chars(inner, exclude="*") + "*")
        else:  # _italic_
            inner = match.group(2)[1:-1]
            out.append("_" + _escape_chars(inner, exclude="_") + "_")
        pos = match.end()
    out.append(_escape_chars(segment[pos:]))
    return "".join(out)


def _headers_to_bold(segment: str) -> str:
    """Convert '# Heading' / '## Heading' lines into '*Heading*' lines."""
    return _HEADER_LINE_RE.sub(lambda m: f"*{m.group(1).strip()}*" if m.group(1).strip() else "", segment)


def _escape_markdown_v2(text: str) -> str:
    """Escape MarkdownV2 special characters outside code fences/inline code,
    converting ATX headers to bold and preserving intentional emphasis.
    """
    parts = _CODE_SPLIT_RE.split(text)
    escaped_parts = []
    for part in parts:
        if part.startswith("```") or (part.startswith("`") and part.endswith("`") and len(part) > 1):
            escaped_parts.append(part)
        else:
            escaped_parts.append(_escape_plain_segment(_headers_to_bold(part)))
    return "".join(escaped_parts)


def _strip_headers_plain(text: str) -> str:
    """Plain-text fallback: drop '#' marks instead of bolding (no Markdown available)."""
    return _HEADER_LINE_RE.sub(lambda m: m.group(1).strip(), text)


def format_reference_block(reference: dict, *, label: str = "Source") -> str:
    """Format a single citation as Subject/Module/Page — never a raw filename.

    Subject comes from the GitHub folder name, module from the PDF
    filename (or folder, for nested layouts), and page from the actual
    indexed page — see app.github_sync.sync_service.classify_subject_module
    and app.search.search_engine for where these are derived.
    """
    subject = reference.get("subject") or "General"
    module = reference.get("module") or "-"
    page = reference.get("page_number")
    page_text = str(page) if page else "-"
    return f"*{label}*\n\nSubject : {subject}\nModule : {module}\nPage : {page_text}"


def format_sources_footer(references: list[dict]) -> str:
    """Legacy multi-source footer (deduplicated by document name).

    Retained for callers that intentionally want several sources listed at
    once (e.g. build_context's multi-document mode). The default
    conversation flow uses format_reference_block for single-source
    citations instead — see rule #4 (source citation).
    """
    if not references:
        return ""
    lines = ["", "_Sources:_"]
    seen = set()
    for ref in references:
        key = ref["document_name"]
        if key in seen:
            continue
        seen.add(key)
        page = f" (p. {ref['page_number']})" if ref.get("page_number") else ""
        lines.append(f"- {ref['document_name']}{page}")
    return "\n".join(lines)


def format_response(
    answer_text: str,
    primary_reference: dict | None = None,
    additional_reference: dict | None = None,
    *,
    escape: bool = True,
) -> list[str]:
    """Prepare the final message text, split into Telegram-sized chunks.

    Args:
        answer_text: Raw LLM output (already assumed to use light Markdown).
        primary_reference: The single best-matching source
            ({subject, module, page_number, ...}), shown as "Source".
        additional_reference: A second source, shown as "Additional
            Reference" — only present when the primary source didn't have
            enough material on its own.
        escape: Whether to escape MarkdownV2 special characters. Set False
            for plain-text fallback delivery if Markdown parsing fails.

    Returns:
        A list of message chunks, each under Telegram's 4096-character limit.
    """
    footer_blocks = []
    if primary_reference:
        footer_blocks.append(format_reference_block(primary_reference, label="Source"))
    if additional_reference:
        footer_blocks.append(format_reference_block(additional_reference, label="Additional Reference"))
    footer = "\n\n".join(footer_blocks)

    body = answer_text if escape else _strip_headers_plain(answer_text)
    full_text = f"{body}\n\n{footer}" if footer else body

    if escape:
        full_text = _escape_markdown_v2(full_text)

    return _chunk_text(full_text, _TELEGRAM_MESSAGE_LIMIT)


def _chunk_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at == -1:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks
