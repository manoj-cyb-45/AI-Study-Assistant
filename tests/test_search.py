"""Tests for app.search.search_engine."""

from __future__ import annotations

from app.search.search_engine import SearchEngine, SearchFilters


def test_search_finds_relevant_section(search_engine: SearchEngine, sample_document: int) -> None:
    results = search_engine.search("deadlock prevention")
    assert len(results) > 0
    assert any("prevention" in r.content.lower() for r in results)


def test_search_respects_subject_filter(search_engine: SearchEngine, sample_document: int) -> None:
    results = search_engine.search("deadlock", SearchFilters(subject="OS"))
    assert all(r.subject == "OS" for r in results)

    no_results = search_engine.search("deadlock", SearchFilters(subject="DBMS"))
    assert no_results == []


def test_search_returns_empty_for_blank_query(search_engine: SearchEngine, sample_document: int) -> None:
    assert search_engine.search("   ") == []


def test_search_results_sorted_best_first_by_composite_score(search_engine: SearchEngine, sample_document: int) -> None:
    results = search_engine.search("deadlock prevention")
    ranks = [r.rank for r in results]
    assert ranks == sorted(ranks, reverse=True)


def test_subject_module_match_boosts_composite_score(search_engine: SearchEngine, sample_document: int) -> None:
    unfiltered = search_engine.search("deadlock")
    boosted = search_engine.search("deadlock", detected_subject="OS", detected_module="Module1")
    # Same underlying rows, but the composite score should be higher once the
    # subject/module match bonus is applied.
    assert boosted[0].rank > unfiltered[0].rank


def test_build_context_respects_char_limit(search_engine: SearchEngine, sample_document: int) -> None:
    tiny_engine = SearchEngine(search_engine.db, result_limit=5, context_char_limit=50)
    results = tiny_engine.search("deadlock")
    context_text, references = tiny_engine.build_context(results)
    assert len(context_text) <= 50 or len(references) <= 1


def test_build_context_includes_references(search_engine: SearchEngine, sample_document: int) -> None:
    results = search_engine.search("deadlock")
    _, references = search_engine.build_context(results)
    assert len(references) > 0
    assert references[0]["document_name"] == "deadlock.pdf"


def test_select_primary_source_picks_the_more_focused_document(
    search_engine: SearchEngine, sample_document: int, second_document: int
) -> None:
    results = search_engine.search("deadlock")
    primary_chunks, secondary_chunks = search_engine.select_primary_source(results)
    assert all(chunk.relative_path == "OS/Module1/deadlock.pdf" for chunk in primary_chunks)
    # The scheduling doc only mentions "deadlock" in passing — it should
    # rank as secondary, not primary, for a "deadlock" query.
    if secondary_chunks:
        assert secondary_chunks[0].relative_path != "OS/Module1/deadlock.pdf"


def test_build_single_source_context_uses_one_document_by_default(
    search_engine: SearchEngine, sample_document: int, second_document: int
) -> None:
    results = search_engine.search("deadlock")
    # sample_document's two sections combined are ~350 chars — comfortably
    # "enough" relative to a modest threshold, so no additional source
    # should be pulled in for a normal query.
    context = search_engine.build_single_source_context(results, min_primary_chars=50)
    assert context.used_repository_content is True
    assert context.primary_reference["document_name"] == "deadlock.pdf"
    assert context.primary_reference["subject"] == "OS"
    assert context.primary_reference["module"] == "Module1"
    assert context.additional_reference is None


def test_build_single_source_context_adds_additional_source_when_primary_is_thin(
    search_engine: SearchEngine, repository, second_document: int
) -> None:
    # A primary document with a very short section should trigger pulling in
    # the additional source.
    doc_id = repository.upsert_document(
        relative_path="OS/Module2/short.pdf", file_name="short.pdf", subject="OS", module="Module2",
        file_hash="ghi789", repo_version="commit1", page_count=1,
    )
    repository.replace_sections(doc_id, [{"page_number": 1, "heading": "Deadlock", "content": "Deadlock is bad."}])

    results = search_engine.search("deadlock")
    context = search_engine.build_single_source_context(results, min_primary_chars=100)
    assert context.additional_reference is not None


def test_build_single_source_context_empty_when_no_results(search_engine: SearchEngine) -> None:
    context = search_engine.build_single_source_context([])
    assert context.used_repository_content is False
    assert context.primary_reference is None
    assert context.context_text == ""
