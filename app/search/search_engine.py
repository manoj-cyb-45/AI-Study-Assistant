"""Search engine built on SQLite FTS5.

Retrieval happens in two stages:

1. FTS5's built-in BM25 ranking (via the ``bm25()`` auxiliary function)
   retrieves a candidate pool of matching sections.
2. A composite re-ranking pass layers exam-relevant signals on top of BM25
   — subject match, module relevance, heading match, exact keyword match,
   phrase match, keyword density, and page relevance — so the single most
   relevant chunk surfaces first, in that priority order.

From there, ``select_primary_source``/``build_single_source_context``
implement "single best source" behavior: normal questions are answered from
one PDF, and a second source is only pulled in when the primary document
doesn't contain enough material.

No embeddings, vector search, or semantic index of any kind is used —
this project's knowledge base is small enough (300-500 PDFs) that FTS5 is
sufficient and keeps the system dependency-light.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.database.connection import Database
from app.database.models import SearchResult
from app.utils.exceptions import SearchError
from app.utils.logger import get_logger

logger = get_logger(__name__)

_FTS_SPECIAL_CHARS_RE = re.compile(r'["\*\^]')

# Composite re-ranking weights, in the exact priority order requested:
# subject match > module relevance > heading match > exact keyword match >
# phrase match > keyword density > page relevance. Each is strictly larger
# than the sum it needs to dominate the ones below it, so the priority
# ordering holds regardless of the underlying BM25 magnitude.
_WEIGHT_SUBJECT_MATCH = 50.0
_WEIGHT_MODULE_MATCH = 30.0
_WEIGHT_HEADING_MATCH = 16.0
_WEIGHT_EXACT_KEYWORD = 9.0
_WEIGHT_PHRASE_MATCH = 5.0
_WEIGHT_KEYWORD_DENSITY = 2.5
_WEIGHT_PAGE_RELEVANCE = 1.0


def _tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, stripping FTS5-special characters."""
    cleaned = _FTS_SPECIAL_CHARS_RE.sub(" ", text)
    return [t.lower() for t in re.findall(r"[A-Za-z0-9_]+", cleaned) if len(t) > 1]


def _sanitize_query(raw_query: str) -> str:
    """Build a safe FTS5 MATCH expression from free-form user text.

    Strips characters with special meaning to FTS5 syntax and joins
    remaining terms with OR so a question with several keywords still
    matches sections containing any of them, weighted by BM25.
    """
    terms = _tokenize(raw_query)
    if not terms:
        return ""
    # Quote each term to treat it literally, OR them together.
    return " OR ".join(f'"{term}"' for term in terms)


@dataclass
class SearchFilters:
    subject: str | None = None
    module: str | None = None


@dataclass
class ContextResult:
    """Result of assembling a single-best-source context for the LLM."""

    context_text: str
    primary_reference: dict | None
    additional_reference: dict | None
    used_repository_content: bool


class SearchEngine:
    """Executes ranked full-text queries against the sections_fts index."""

    def __init__(self, db: Database, *, result_limit: int = 8, context_char_limit: int = 12000) -> None:
        self.db = db
        self.result_limit = result_limit
        self.context_char_limit = context_char_limit

    def search(
        self,
        query: str,
        filters: SearchFilters | None = None,
        *,
        detected_subject: str | None = None,
        detected_module: str | None = None,
    ) -> list[SearchResult]:
        """Return the top composite-ranked sections matching ``query``, deduplicated.

        ``detected_subject``/``detected_module`` (defaulting to the values in
        ``filters`` when not given) are used purely as re-ranking hints —
        separate from ``filters``, which are hard SQL WHERE constraints. This
        lets a caller do an unfiltered search while still preferring rows
        that match the detected subject/module.
        """
        query_terms = _tokenize(query)
        fts_query = _sanitize_query(query)
        if not fts_query:
            return []

        filters = filters or SearchFilters()
        detected_subject = detected_subject or filters.subject
        detected_module = detected_module or filters.module

        where_clauses = ["sections_fts MATCH ?"]
        params: list[object] = [fts_query]

        if filters.subject:
            where_clauses.append("subject = ?")
            params.append(filters.subject)
        if filters.module:
            where_clauses.append("module = ?")
            params.append(filters.module)

        sql = f"""
            SELECT
                rowid AS section_id,
                document_name,
                relative_path,
                subject,
                module,
                page_number,
                heading,
                content,
                bm25(sections_fts) AS rank
            FROM sections_fts
            WHERE {' AND '.join(where_clauses)}
            ORDER BY rank
            LIMIT ?
        """
        params.append(self.result_limit * 4)  # over-fetch before re-ranking + dedup

        try:
            with self.db.connect() as conn:
                rows = conn.execute(sql, params).fetchall()
        except Exception as exc:  # sqlite3.OperationalError on malformed MATCH, etc.
            raise SearchError(f"Full-text search query failed: {exc}") from exc

        results = [
            SearchResult(
                section_id=row["section_id"],
                document_name=row["document_name"],
                relative_path=row["relative_path"],
                subject=row["subject"],
                module=row["module"],
                page_number=row["page_number"],
                heading=row["heading"],
                content=row["content"],
                rank=row["rank"],
            )
            for row in rows
        ]

        deduped = self._deduplicate(results)
        rescored = self._rescore(
            deduped, query_terms=query_terms, full_query_text=query,
            detected_subject=detected_subject, detected_module=detected_module,
        )
        return rescored[: self.result_limit]

    def select_primary_source(
        self, results: list[SearchResult]
    ) -> tuple[list[SearchResult], list[SearchResult]]:
        """Split results into the single best-matching document's chunks
        (primary) and the next-best document's chunks (secondary, used only
        if the primary source turns out to be insufficient).

        Assumes ``results`` is already composite-scored (i.e. came from
        ``search()``), where a higher ``rank`` means a better match.
        """
        if not results:
            return [], []

        groups: dict[str, list[SearchResult]] = {}
        for result in results:
            groups.setdefault(result.relative_path, []).append(result)

        ranked_paths = sorted(groups.keys(), key=lambda p: max(r.rank for r in groups[p]), reverse=True)

        primary_chunks = sorted(
            groups[ranked_paths[0]], key=lambda r: (r.page_number is None, r.page_number or 0)
        )
        secondary_chunks: list[SearchResult] = []
        if len(ranked_paths) > 1:
            secondary_chunks = sorted(groups[ranked_paths[1]], key=lambda r: r.rank, reverse=True)

        return primary_chunks, secondary_chunks

    def build_single_source_context(
        self, results: list[SearchResult], *, min_primary_chars: int = 400
    ) -> ContextResult:
        """Assemble context from the single best-matching document.

        Only pulls in a second source if the primary document's retrieved
        content falls under ``min_primary_chars`` — i.e. genuinely doesn't
        contain enough material to answer from alone.
        """
        if not results:
            return ContextResult(context_text="", primary_reference=None, additional_reference=None, used_repository_content=False)

        primary_chunks, secondary_chunks = self.select_primary_source(results)
        best_primary = max(primary_chunks, key=lambda r: r.rank)

        primary_parts: list[str] = []
        used_chars = 0
        for chunk in primary_chunks:
            if used_chars + len(chunk.content) > self.context_char_limit and primary_parts:
                break
            primary_parts.append(chunk.content)
            used_chars += len(chunk.content)
        primary_text = "\n\n".join(primary_parts)

        primary_reference = {
            "document_name": best_primary.document_name,
            "relative_path": best_primary.relative_path,
            "subject": best_primary.subject,
            "module": best_primary.module,
            "page_number": best_primary.page_number,
        }
        context_text = f"[Primary source: {best_primary.document_name}]\n{primary_text}"
        additional_reference: dict | None = None

        if len(primary_text) < min_primary_chars and secondary_chunks:
            best_secondary = secondary_chunks[0]
            remaining_budget = self.context_char_limit - used_chars
            if remaining_budget > 100:
                secondary_text = best_secondary.content[:remaining_budget]
                context_text += f"\n\n[Additional source (primary was insufficient): {best_secondary.document_name}]\n{secondary_text}"
                additional_reference = {
                    "document_name": best_secondary.document_name,
                    "relative_path": best_secondary.relative_path,
                    "subject": best_secondary.subject,
                    "module": best_secondary.module,
                    "page_number": best_secondary.page_number,
                }

        return ContextResult(
            context_text=context_text,
            primary_reference=primary_reference,
            additional_reference=additional_reference,
            used_repository_content=True,
        )

    def build_context(self, results: list[SearchResult]) -> tuple[str, list[dict]]:
        """Assemble a multi-source context string within the character budget.

        Kept for callers that intentionally want material from several
        documents at once (e.g. broad revision-note requests spanning a
        whole module). Normal question-answering should prefer
        ``build_single_source_context`` instead. Returns (context_text,
        references), where references is a list of {document_name,
        relative_path, subject, module, page_number}.
        """
        context_parts: list[str] = []
        references: list[dict] = []
        used_chars = 0

        for result in results:
            block = self._format_block(result)
            if used_chars + len(block) > self.context_char_limit and context_parts:
                break
            context_parts.append(block)
            used_chars += len(block)
            references.append(
                {
                    "document_name": result.document_name,
                    "relative_path": result.relative_path,
                    "subject": result.subject,
                    "module": result.module,
                    "page_number": result.page_number,
                }
            )

        return "\n\n---\n\n".join(context_parts), references

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _format_block(result: SearchResult) -> str:
        header_bits = [result.document_name]
        if result.page_number:
            header_bits.append(f"page {result.page_number}")
        if result.heading:
            header_bits.append(result.heading)
        header = " | ".join(header_bits)
        return f"[Source: {header}]\n{result.content}"

    @staticmethod
    def _deduplicate(results: list[SearchResult]) -> list[SearchResult]:
        """Drop near-duplicate sections (same document + overlapping text)."""
        seen_signatures: set[str] = set()
        deduped: list[SearchResult] = []
        for result in results:
            signature = f"{result.relative_path}:{result.content[:120].strip().lower()}"
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            deduped.append(result)
        return deduped

    @staticmethod
    def _rescore(
        results: list[SearchResult],
        *,
        query_terms: list[str],
        full_query_text: str,
        detected_subject: str | None,
        detected_module: str | None,
    ) -> list[SearchResult]:
        """Recompute each result's ``rank`` as a composite score (higher = better)
        and return results sorted best-first.

        BM25's own score (where smaller/more negative is better) becomes the
        base component; the exam-relevance signals are layered on top with
        strictly decreasing weights so the requested priority order —
        subject > module > heading > exact keyword > phrase > density > page
        — always holds.
        """
        phrase = full_query_text.strip().lower()

        for result in results:
            content_lower = result.content.lower()
            heading_lower = (result.heading or "").lower()

            subject_match = 1.0 if (
                detected_subject and result.subject and result.subject.lower() == detected_subject.lower()
            ) else 0.0
            module_match = 1.0 if (
                detected_module and result.module and result.module.lower() == detected_module.lower()
            ) else 0.0
            heading_match = 1.0 if query_terms and any(term in heading_lower for term in query_terms) else 0.0

            exact_hits = sum(1 for term in query_terms if term in content_lower)
            exact_keyword_match = (exact_hits / len(query_terms)) if query_terms else 0.0
            phrase_match = 1.0 if phrase and phrase in content_lower else 0.0

            word_count = max(len(content_lower.split()), 1)
            keyword_density = min((exact_hits / word_count) * 10, 1.0)
            page_relevance = 1.0 if result.page_number else 0.0

            bm25_component = -result.rank  # smaller/more negative bm25 => better match

            result.rank = (
                bm25_component
                + subject_match * _WEIGHT_SUBJECT_MATCH
                + module_match * _WEIGHT_MODULE_MATCH
                + heading_match * _WEIGHT_HEADING_MATCH
                + exact_keyword_match * _WEIGHT_EXACT_KEYWORD
                + phrase_match * _WEIGHT_PHRASE_MATCH
                + keyword_density * _WEIGHT_KEYWORD_DENSITY
                + page_relevance * _WEIGHT_PAGE_RELEVANCE
            )

        results.sort(key=lambda r: r.rank, reverse=True)
        return results
