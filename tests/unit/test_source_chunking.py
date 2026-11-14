"""Complete, bounded source views preserve raw shared clauses and page identity."""

from __future__ import annotations

import pytest

from scione.schemas import DocumentPage, IngestedDocument
from scione.specifications.source_chunking import (
    SourceChunkPlanningError,
    SourceContextSpan,
    plan_source_chunks,
    shared_context_pages,
)


def document(texts: list[str]) -> IngestedDocument:
    return IngestedDocument(
        document_id="original-document", file_name="customer.pdf", sha256="a" * 64,
        page_count=len(texts), extraction_status="ready", text_reading_order="geometric",
        pages=[DocumentPage(
            page_number=index, text=text, character_count=len(text),
            non_whitespace_character_count=len("".join(text.split())),
        ) for index, text in enumerate(texts, 1)],
    )


def test_raw_shared_sections_continue_until_another_section_and_index_stops_them():
    source = document([
        "Repeated title\n1. Document control\nCustomer identity.",
        "Repeated title\n2. General test conditions\nUse a 20 °C bath for all tests.",
        "Repeated title\nContinued shared sampling rules.\n2.4 Test item index\nGloss 3.1",
        "Repeated title\nRemaining index rows.\n3. Appearance\nGloss test 60 degrees.",
        "Repeated title\n4. Regional annex\nApproved alternatives require approval.",
        "Repeated title\nContinued regional exceptions.\n5. Local test\nOne other test.",
    ])
    assert shared_context_pages(source) == (1, 2, 3, 5, 6)


def test_complete_request_budget_is_measured_and_every_page_is_owned_once():
    source = document(["Title", *[f"Test page {index}: " + "x" * 90 for index in range(2, 9)]])
    measured = []

    def size(chunk):
        measured.append(chunk)
        return 500 + len(chunk.document.as_prompt_text())

    chunks = plan_source_chunks(source, request_size=size, max_input_characters=870)
    assert len(chunks) > 1
    assert tuple(n for chunk in chunks for n in chunk.primary_pages) == tuple(range(1, 9))
    assert all(500 + len(chunk.document.as_prompt_text()) <= 870 for chunk in chunks)
    assert all(chunk.document.document_id == source.document_id for chunk in chunks)
    assert all(chunk.document.sha256 == source.sha256 for chunk in chunks)
    assert all(chunk.document.page_count == 8 for chunk in chunks)
    assert all(chunk.document.pages[0].page_number == 1 for chunk in chunks)
    assert all(
        page.text == source.pages[page.page_number - 1].text
        for chunk in chunks for page in chunk.document.pages
    )
    assert len(measured) >= len(chunks)


def test_repeated_shared_headings_and_numeric_conditions_are_not_page_furniture():
    source = document([
        "Document title\n1. General conditions\n7 days at room temperature.",
        "Document title\n1. General conditions\nContinue with 50 % humidity.",
        "Document title\n2. Test methods\nOne local method.",
    ])
    assert shared_context_pages(source) == (1, 2)


def test_adjacent_page_overlap_is_optional_before_primary_range_is_reduced():
    source = document([f"Page {index}" for index in range(1, 8)])
    chunks = plan_source_chunks(
        source, request_size=lambda chunk: len(chunk.document.pages),
        max_input_characters=4, max_primary_pages=3,
    )
    assert chunks[0].primary_pages == (1, 2, 3)
    assert chunks[0].context_pages == (4,)
    assert chunks[1].primary_pages == (4, 5, 6)
    assert tuple(p.page_number for p in chunks[1].document.pages) == (1, 4, 5, 6)


def test_dense_primary_pages_are_separated_even_when_the_input_budget_fits():
    source = document(["Title", "a" * 2500, "b" * 2500, "c" * 1000])
    chunks = plan_source_chunks(
        source, request_size=lambda chunk: len(chunk.document.as_prompt_text()),
        max_input_characters=20_000,
    )
    assert [chunk.primary_pages for chunk in chunks] == [(1, 2), (3, 4)]
    assert tuple(n for chunk in chunks for n in chunk.primary_pages) == (1, 2, 3, 4)


def test_repeated_shared_text_does_not_consume_the_primary_output_work_budget():
    source = document([
        "1. General conditions\n" + "x" * 5000,
        "Shared continuation\n" + "x" * 5000,
        "2. Test methods\n" + "a" * 1000,
        "b" * 1000,
    ])
    chunks = plan_source_chunks(
        source, request_size=lambda chunk: len(chunk.document.as_prompt_text()),
        max_input_characters=20_000,
    )
    assert chunks[0].primary_pages == (1, 2, 3)
    assert all({1, 2}.issubset({p.page_number for p in c.document.pages}) for c in chunks)


def test_indivisible_dense_page_is_kept_whole_and_soft_work_cap_can_be_disabled():
    source = document(["Title", "x" * 6000])

    def size(chunk):
        return len(chunk.document.as_prompt_text())

    chunks = plan_source_chunks(source, request_size=size, max_input_characters=20_000)
    assert [chunk.primary_pages for chunk in chunks] == [(1,), (2,)]
    assert chunks[1].document.pages[-1].text == source.pages[-1].text
    uncapped = plan_source_chunks(
        source, request_size=size, max_input_characters=20_000, max_primary_characters=None,
    )
    assert [chunk.primary_pages for chunk in uncapped] == [(1, 2)]


def test_shared_raw_context_is_never_silently_dropped_to_make_a_chunk_fit():
    source = document([
        "1. General conditions\n" + "x" * 100,
        "Shared continuation.\n" + "x" * 100,
        "2. Test requirements\nAdhesion at 25 °C.",
    ])
    with pytest.raises(SourceChunkPlanningError, match="required shared context"):
        plan_source_chunks(
            source, request_size=lambda chunk: len(chunk.document.as_prompt_text()),
            max_input_characters=150,
        )


def test_call_limit_and_individually_oversized_page_fail_before_a_partial_plan_is_returned():
    source = document(["Title", "x" * 50, "y" * 50, "z" * 50])
    with pytest.raises(SourceChunkPlanningError, match="more than 2 model calls"):
        plan_source_chunks(
            source, request_size=lambda chunk: len(chunk.document.pages),
            max_input_characters=2, max_primary_pages=1, max_calls=2,
        )
    with pytest.raises(SourceChunkPlanningError, match="Physical page 2"):
        plan_source_chunks(
            source, request_size=lambda chunk: len(chunk.document.as_prompt_text()),
            max_input_characters=60,
        )


def test_incomplete_or_out_of_order_physical_page_input_is_rejected():
    source = document(["one", "two", "three"])
    source.pages = source.pages[:1] + source.pages[2:]
    with pytest.raises(SourceChunkPlanningError, match="every original physical page"):
        plan_source_chunks(source, request_size=lambda _: 0, max_input_characters=100)


@pytest.mark.parametrize("field", [
    "max_input_characters", "max_calls", "max_primary_pages", "max_primary_characters",
])
def test_budgets_must_be_positive_integers(field):
    source = document(["one"])
    arguments = {"request_size": lambda _: 0, "max_input_characters": 100, field: True}
    with pytest.raises(ValueError, match=field):
        plan_source_chunks(source, **arguments)


def reference_document(*, reference="2.1", duplicate=False):
    return document([
        "Repeated document title\n1. Title\nCustomer specification.",
        "Repeated document title\n2.1 Weathering\nSee Section 6.1 for optional sample handling.",
        "Repeated document title\nWeathering continuation: expose for 500 h at 40 °C.",
        "Repeated document title\n2.2 Chemical resistance\nUse the selected medium.",
        "Repeated document title\n" + (
            "2.1 Duplicate heading\nDifferent weathering details."
            if duplicate else "5. Mechanical requirements\nLocal testing."
        ),
        "Repeated document title\n6.1 Sampling\nRetain every specimen identifier.",
        "Repeated document title\n7. Local testing\nOther independent test.",
        f"Repeated document title\n8. Regional test\nUse Section {reference}.",
    ])


def one_page_chunks(source, **kwargs):
    return plan_source_chunks(
        source, request_size=lambda chunk: len(chunk.document.pages),
        max_input_characters=8, max_primary_pages=1, **kwargs,
    )


def test_primary_section_reference_adds_complete_raw_range_with_continuation():
    source = reference_document()
    chunks = one_page_chunks(source)
    target = chunks[-1]
    assert target.primary_pages == (8,)
    assert target.referenced_context_pages == (2, 3)
    assert {2, 3}.issubset(target.context_pages)
    assert tuple(n for chunk in chunks for n in chunk.primary_pages) == tuple(range(1, 9))
    for chunk in chunks:
        for page in chunk.document.pages:
            original = source.pages[page.page_number - 1]
            spans = [s for s in chunk.context_source_spans if s.page_number == page.page_number]
            if spans:
                assert page.text == "".join(original.text[s.start:s.end] for s in spans)
                assert page.page_number not in chunk.primary_pages
                assert page.page_number not in chunk.shared_context_pages
            else:
                assert page.text == original.text
            assert page.character_count == len(page.text)


def test_reference_uses_its_actual_number_and_does_not_leak_from_context_pages():
    source = reference_document(reference="6.1")
    target = one_page_chunks(source)[-1]
    assert target.referenced_context_pages == (6,)
    source = reference_document()
    chunks = one_page_chunks(source)
    # Primary p2 has a direct 6.1 reference, so it gets p6. Primary p8 only
    # references 2.1: that section's reference to 6.1 is not followed recursively.
    assert chunks[1].referenced_context_pages == (6,)
    assert chunks[-1].referenced_context_pages == (2, 3)
    assert 6 not in {page.page_number for page in chunks[-1].document.pages}
    assert chunks[4].referenced_context_pages == ()


@pytest.mark.parametrize("cause", ["unknown", "ambiguous"])
def test_unknown_or_ambiguous_number_never_selects_an_arbitrary_section(cause):
    source = reference_document(
        reference="9.9" if cause == "unknown" else "2.1", duplicate=cause == "ambiguous",
    )
    target = one_page_chunks(source)[-1]
    assert target.referenced_context_pages == ()
    assert 2 not in {page.page_number for page in target.document.pages}
    assert target.document.pages[-1].text == source.pages[-1].text


def test_section_context_is_mandatory_even_when_optional_overlap_is_removed():
    source = reference_document()
    with pytest.raises(SourceChunkPlanningError, match="section references"):
        plan_source_chunks(
            source, request_size=lambda chunk: len(chunk.document.pages),
            max_input_characters=3, max_primary_pages=1,
        )


def test_shared_context_reference_is_not_repeated_as_mandatory_for_every_chunk():
    source = reference_document()
    source.pages[0].text += "\nUse Section 6.1."
    chunks = one_page_chunks(source)
    assert chunks[0].referenced_context_pages == (6,)
    assert chunks[3].referenced_context_pages == ()
    assert 6 not in {page.page_number for page in chunks[3].document.pages}


def test_referenced_neighbor_stays_exact_section_fragment_instead_of_whole_optional_page():
    source = document([
        "Title",
        "Page furniture\n2.1 Needed section\nKeep the 40 °C limit.\n"
        "2.2 Different section\nUnrelated chemical tests must stay out.\n",
        "3. Local request\nUse Section 2.1.",
    ])
    before = source.model_dump_json()
    target = one_page_chunks(source)[-1]
    supplied = next(page for page in target.document.pages if page.page_number == 2)
    start = source.pages[1].text.index("2.1 Needed")
    end = source.pages[1].text.index("2.2 Different")
    assert supplied.text == source.pages[1].text[start:end]
    assert "40 °C" in supplied.text
    assert "Unrelated chemical" not in supplied.text
    assert [(s.page_number, s.start, s.end) for s in target.context_source_spans] == [
        (2, start, end),
    ]
    primary = one_page_chunks(source)[1]
    primary_page = next(p for p in primary.document.pages if p.page_number == 2)
    assert primary_page.text == source.pages[1].text
    assert all(s.page_number != 2 for s in primary.context_source_spans)
    assert source.model_dump_json() == before


def test_multiple_references_union_disjoint_source_spans_without_invented_text():
    source = document([
        "Title",
        "2.1 First requested\nKeep A at -20 °C.\n"
        "2.2 Unrelated\nDiscard B only from this reference view.\n"
        "2.3 Third requested\nKeep C at 70 °C.\n",
        "3. Local request\nUse Section 2.1 and Section 2.3.",
    ])
    target = one_page_chunks(source)[-1]
    spans = target.context_source_spans
    assert len(spans) == 2
    assert all(span.page_number == 2 for span in spans)
    assert spans[0].end < spans[1].start
    supplied = next(page for page in target.document.pages if page.page_number == 2)
    assert supplied.text == "".join(source.pages[1].text[s.start:s.end] for s in spans)
    assert "Keep A at -20 °C" in supplied.text and "Keep C at 70 °C" in supplied.text
    assert "Discard B" not in supplied.text


def test_overlapping_parent_and_child_sections_are_not_duplicated():
    source = document([
        "Title",
        "2.1 Parent section\nParent rule.\n2.1.1 Child section\nChild rule.\n"
        "2.2 Other section\nOther rule.\n",
        "3. Local request\nUse Section 2.1 and Section 2.1.1.",
    ])
    target = one_page_chunks(source)[-1]
    assert len(target.context_source_spans) == 1
    supplied = next(page for page in target.document.pages if page.page_number == 2)
    assert supplied.text.count("Child rule.") == 1
    assert "Other rule." not in supplied.text


def test_shared_pages_remain_whole_even_when_they_are_referenced():
    source = document([
        "Title",
        "Page furniture\n2.1 General conditions\nUse a 40 °C bath.\n"
        "2.2 Additional conditions\nRetain this separate shared clause.\n",
        "3. Local request\nUse Section 2.1.",
    ])
    target = one_page_chunks(source)[-1]
    assert 2 in target.shared_context_pages
    assert all(span.page_number != 2 for span in target.context_source_spans)
    assert next(p for p in target.document.pages if p.page_number == 2).text == source.pages[1].text


def test_reference_span_continuations_preserve_intermediate_pages_whole():
    source = document([
        "Title", "Unrelated preface\n2.1 Weathering\nStart the exposure.",
        "Intermediate exposure details at 40 °C.\nNothing is shortened here.\n",
        "Final exposure details.\n2.2 Different work\nDifferent row.",
        "5. Other work\nOther row.", "6. Local request\nUse Section 2.1.",
    ])
    target = one_page_chunks(source)[-1]
    assert target.referenced_context_pages == (2, 3, 4)
    middle = next(p for p in target.document.pages if p.page_number == 3)
    assert middle.text == source.pages[2].text
    assert any(s.page_number == 3 and s.start == 0 and s.end == len(middle.text)
               for s in target.context_source_spans)
    last = next(p for p in target.document.pages if p.page_number == 4)
    assert last.text == source.pages[3].text[:source.pages[3].text.index("2.2 Different")]


def test_request_budget_measures_actual_reference_fragment_instead_of_full_context_page():
    source = document([
        "Title",
        "2.1 Needed section\nUse 40 °C.\n2.2 Unrelated section\n" + "z" * 8000,
        "3. Local request\nUse Section 2.1.\n" + "x" * 4500,
    ])
    chunks = plan_source_chunks(
        source, request_size=lambda c: len(c.document.as_prompt_text()),
        max_input_characters=10_000, max_primary_pages=1,
    )
    assert all(len(c.document.as_prompt_text()) <= 10_000 for c in chunks)
    assert len(source.pages[1].text) + len(source.pages[2].text) > 10_000
    target = chunks[-1]
    context = next(p for p in target.document.pages if p.page_number == 2)
    assert context.text == "2.1 Needed section\nUse 40 °C.\n"
    original_primary = next(p for p in chunks[1].document.pages if p.page_number == 2)
    assert original_primary.text == source.pages[1].text


def continuation_intro_source():
    source = document([
        "Title",
        "Page header\n2.1 Exposure\nUse low-bake panels and assess after 24 h.\n"
        "First exposure row: 50 °C.\nOther body rows stay out of intro context.\n",
        "Exposure table continuation: resin at 60 °C.",
        "2.2 Other section\nIndependent work.",
    ])
    text = source.pages[1].text
    intro = SourceContextSpan(2, text.index("2.1 Exposure"), text.index("First exposure row"))
    return source, intro


def test_section_intro_is_mandatory_for_continued_primary_page_without_restoring_body_rows():
    source, intro = continuation_intro_source()
    before = source.model_dump_json()
    chunks = one_page_chunks(source, section_intro_spans=(("2.1", intro),))
    continuation = chunks[2]
    assert continuation.primary_pages == (3,)
    assert continuation.section_context_pages == (2,)
    assert continuation.referenced_context_pages == ()
    assert continuation.context_source_spans == (intro,)
    context = next(page for page in continuation.document.pages if page.page_number == 2)
    assert context.text == source.pages[1].text[intro.start:intro.end]
    assert "24 h" in context.text and "First exposure row" not in context.text
    assert next(p for p in continuation.document.pages if p.page_number == 3).text == (
        source.pages[2].text
    )
    original_primary = next(p for p in chunks[1].document.pages if p.page_number == 2)
    assert original_primary.text == source.pages[1].text
    assert chunks[1].section_context_pages == ()
    assert source.model_dump_json() == before


def test_intro_and_literal_reference_spans_share_a_union_with_separate_origin_metadata():
    source = document([
        "Title",
        "2.1 Cited section\nCited body.\n2.2 Continued section\nUse 24 h recovery.\n"
        "Unrelated body rows in this section.\n",
        "Continuation of exposure rows; use Section 2.1.",
        "2.3 Other section\nOther work.",
    ])
    text = source.pages[1].text
    intro = SourceContextSpan(2, text.index("2.2 Continued"), text.index("Unrelated body"))
    chunk = one_page_chunks(source, section_intro_spans=(("2.2", intro),))[2]
    assert chunk.referenced_context_pages == chunk.section_context_pages == (2,)
    assert len(chunk.context_source_spans) == 1
    span = chunk.context_source_spans[0]
    assert (span.page_number, span.start, span.end) == (2, 0, intro.end)
    assert next(p for p in chunk.document.pages if p.page_number == 2).text == text[:intro.end]


@pytest.mark.parametrize("invalid", [
    "unknown_section", "ambiguous_section", "duplicate_section", "negative_offset", "too_long",
    "empty",
    "wrong_section_page", "boolean_offset", "wrong_span_type",
])
def test_section_intro_metadata_fails_before_planning_when_not_source_contained(invalid):
    source, intro = continuation_intro_source()
    entry = ("2.1", intro)
    if invalid == "unknown_section":
        entry = ("9.9", intro)
    elif invalid == "ambiguous_section":
        source.pages[2].text = "2.1 Duplicate heading\nUnrelated subsection."
    elif invalid == "negative_offset":
        entry = ("2.1", SourceContextSpan(2, -1, intro.end))
    elif invalid == "too_long":
        entry = ("2.1", SourceContextSpan(2, intro.start, len(source.pages[1].text) + 1))
    elif invalid == "empty":
        entry = ("2.1", SourceContextSpan(2, intro.start, intro.start))
    elif invalid == "wrong_section_page":
        entry = ("2.1", SourceContextSpan(4, 0, 10))
    elif invalid == "boolean_offset":
        entry = ("2.1", SourceContextSpan(2, True, intro.end))
    elif invalid == "wrong_span_type":
        entry = ("2.1", (2, intro.start, intro.end))
    entries = (entry, entry) if invalid == "duplicate_section" else (entry,)
    with pytest.raises(ValueError, match="section_intro_spans"):
        one_page_chunks(source, section_intro_spans=entries)


def test_section_intro_cannot_be_dropped_to_satisfy_request_budget():
    source, intro = continuation_intro_source()
    with pytest.raises(SourceChunkPlanningError, match="Physical page 3"):
        plan_source_chunks(
            source, request_size=lambda c: len(c.document.pages), max_input_characters=2,
            max_primary_pages=1, section_intro_spans=(("2.1", intro),),
        )


def test_absent_section_intro_argument_preserves_the_default_plan_exactly():
    source, _ = continuation_intro_source()
    assert one_page_chunks(source) == one_page_chunks(source, section_intro_spans=())


def test_client_references_keep_real_continuation_pages_within_complete_request_budget():
    import json
    from pathlib import Path

    from scione.ingestion.pdf_text import PyMuPDFTextIngestor
    from scione.prompts.customer_requirements import (
        build_customer_requirements_user_prompt,
        load_compact_customer_requirements_system_prompt,
    )
    from scione.schemas import TextReadingOrder
    from scione.specifications.extraction import _structured_customer_schema
    from scione.specifications.models import CompactCustomerExtraction
    from scione.specifications.output_schema import constrain_compact_extraction_schema
    from scione.specifications.section_scopes import source_section_intros
    from scione.specifications.source_chunking import _section_source_spans

    source = PyMuPDFTextIngestor(reading_order=TextReadingOrder.GEOMETRIC).ingest(
        Path(__file__).resolve().parents[2] / "docs/samples/MS-PNT-410_demo_spec.pdf"
    )
    schema = constrain_compact_extraction_schema(
        _structured_customer_schema(CompactCustomerExtraction.model_json_schema())
    )
    system = load_compact_customer_requirements_system_prompt()

    def actual_request_size(chunk):
        return (
            len(system)
            + len(build_customer_requirements_user_prompt(
                chunk.document, focus_pages=chunk.primary_pages,
                chunk_index=chunk.index, chunk_count=8, context_pages=chunk.context_pages,
                fragment_pages=tuple(sorted({s.page_number for s in chunk.context_source_spans})),
            ))
            + len(json.dumps(schema, ensure_ascii=False))
        )

    chunks = plan_source_chunks(
        source, request_size=actual_request_size, max_input_characters=60_000,
    )
    assert [chunk.primary_pages for chunk in chunks] == [
        (1, 2, 3), (4, 5, 6), (7, 8), (9, 10, 11), (12, 13),
    ]
    assert chunks[2].referenced_context_pages == (9, 10)  # Section 4.4 continues on p10.
    assert chunks[3].referenced_context_pages == (7, 8)  # Section 4.2 continues on p8.
    assert chunks[4].referenced_context_pages == (7, 8)  # Annex's direct Section 4.2 ref.
    p9 = next(p for p in chunks[2].document.pages if p.page_number == 9)
    assert p9.text.startswith(" 4.4 Color difference after weathering\n")
    assert "Tree resin" not in p9.text and "Gasoline E10" not in p9.text
    p10 = next(p for p in chunks[2].document.pages if p.page_number == 10)
    assert "5. Repair and compatibility" not in p10.text
    p8 = next(p for p in chunks[4].document.pages if p.page_number == 8)
    assert "4.3 Chemical and fluid resistance" not in p8.text
    assert "Deionized water" not in p8.text
    for chunk in chunks:
        for number in chunk.primary_pages:
            assert next(p for p in chunk.document.pages if p.page_number == number).text == (
                source.pages[number - 1].text
            )
    assert all(actual_request_size(chunk) <= 60_000 for chunk in chunks)
    assert tuple(n for chunk in chunks for n in chunk.primary_pages) == tuple(range(1, 14))
    sections = _section_source_spans(source)
    pdf_path = Path(__file__).resolve().parents[2] / "docs/samples/MS-PNT-410_demo_spec.pdf"
    introductions = tuple((intro.section_id, SourceContextSpan(
        intro.page_number, sections[intro.section_id][0].start, intro.end,
    )) for intro in source_section_intros(source, pdf_path.read_bytes()))
    carried = plan_source_chunks(
        source, request_size=actual_request_size, max_input_characters=60_000,
        section_intro_spans=introductions,
    )
    assert [c.primary_pages for c in carried] == [c.primary_pages for c in chunks]
    continuation = carried[3]  # Primary 9–11 includes the chemical continuation.
    assert continuation.section_context_pages == (8,)
    context = next(page for page in continuation.document.pages if page.page_number == 8)
    assert "4.3 Chemical and fluid resistance" in context.text
    assert "Judged 24 h after exposure on low-bake panels." in context.text
    assert "Deionized water" not in context.text and "Sulfuric acid" not in context.text
    assert all(actual_request_size(chunk) <= 60_000 for chunk in carried)


def test_chunk_vocabulary_retains_all_ids_and_labels_and_short_prompt_keeps_descriptions():
    import re

    from scione.parameters import definitions
    from scione.prompts.customer_requirements import build_customer_requirements_user_prompt

    source = document(["Single ordinary source page with the original customer request."])

    def vocabulary_lines(prompt):
        match = re.search(
            r"<PARAMETER_VOCABULARY[^>]*>\n(.*?)\n</PARAMETER_VOCABULARY>", prompt, re.S,
        )
        assert match is not None
        return match[1].splitlines()[1:]  # Exclude the shared naming-hints instruction.

    full_lines = [f"{item.id}: {item.label}; {item.description}" for item in definitions()]
    labels = [f"{item.id}: {item.label}" for item in definitions()]
    assert len(full_lines) == len(labels) == 40
    assert vocabulary_lines(build_customer_requirements_user_prompt(source)) == full_lines
    assert vocabulary_lines(build_customer_requirements_user_prompt(
        source, focus_pages=(1,),
    )) == full_lines
    chunk_prompt = build_customer_requirements_user_prompt(
        source, focus_pages=(1,), chunk_index=1, chunk_count=1,
    )
    assert vocabulary_lines(chunk_prompt) == labels
    assert all(page.text.rstrip() in chunk_prompt for page in source.pages)
    assert "=== PAGE 1 ===\nPAGE_ROLE: PRIMARY\n" in chunk_prompt


def test_chunk_page_roles_leave_original_page_bodies_and_short_rendering_unchanged():
    from scione.prompts.customer_requirements import build_customer_requirements_user_prompt

    source = document([
        "Customer identity\n  Keep original indentation and  double spaces.\n",
        "4.3 Chemical and fluid resistance\nJudged 24 h after exposure.\n",
        "Medium     Method      Requirement\nTree resin  Gradient oven  No damage below 50 °C.\n",
    ])
    before = source.model_dump_json()

    def body(prompt):
        return prompt.split("<DOCUMENT_TEXT>\n", 1)[1].split("\n</DOCUMENT_TEXT>", 1)[0]

    whole_prompt = build_customer_requirements_user_prompt(source)
    focused_prompt = build_customer_requirements_user_prompt(source, focus_pages=(2,))
    assert body(whole_prompt) == body(focused_prompt) == source.as_prompt_text()
    assert "PAGE_ROLE:" not in body(whole_prompt)
    chunk_prompt = build_customer_requirements_user_prompt(
        source, focus_pages=(2,), chunk_index=2, chunk_count=3, context_pages=(1, 3),
    )
    expected = "\n\n".join(
        f"=== PAGE {page.page_number} ===\n"
        f"PAGE_ROLE: {'PRIMARY' if page.page_number == 2 else 'CONTEXT_ONLY'}\n"
        f"{page.text.rstrip()}"
        for page in source.pages
    )
    assert body(chunk_prompt) == expected
    assert source.model_dump_json() == before


def test_primary_section_heading_does_not_own_independent_context_table_rows_in_prompt():
    """Check boundary instructions and raw transport, not model compliance."""
    from scione.prompts.customer_requirements import build_customer_requirements_user_prompt

    source = document([
        "4.3 Chemical and fluid resistance\nJudged 24 h after exposure.",
        "Medium      Method        Requirement\nTree resin  Gradient oven  No damage below 50 °C.",
    ])
    prompt = build_customer_requirements_user_prompt(
        source, focus_pages=(1,), chunk_index=1, chunk_count=2, context_pages=(2,),
    )
    rules = prompt.split("<SOURCE_CHUNK", 1)[1].split("</SOURCE_CHUNK>", 1)[0]
    assert "substantive test row or procedure request occurs on a primary page" in rules
    assert (
        "A section heading, index, cross-reference or shared clause does not own separate "
        "rows printed on context pages."
    ) in rules
    assert "Context pages may complete the same continued primary row" in rules
    assert "An annex may own explicitly additional tests" in rules
    assert "package summaries are not new tests" in rules
    assert "PAGE_ROLE labels are orchestration metadata, not customer text" in rules
    assert "introduced or detailed on primary pages" not in rules
    assert f"=== PAGE 1 ===\nPAGE_ROLE: PRIMARY\n{source.pages[0].text}" in prompt
    assert f"=== PAGE 2 ===\nPAGE_ROLE: CONTEXT_ONLY\n{source.pages[1].text}" in prompt
