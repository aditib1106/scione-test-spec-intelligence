"""Offline checks for triage, not model accuracy or calibrated confidence."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pymupdf
import pytest

from scione.providers import TokenUsage
from scione.schemas import (
    DocumentPage,
    IngestedDocument,
    TextExtractionStatus,
    TextReadingOrder,
)
from scione.specifications.extraction import coverage_issues, validate_customer_sources
from scione.specifications.models import (
    CustomerDocumentMetadata,
    CustomerExtraction,
    CustomerExtractionRun,
    CustomerRequirement,
    RequestedMethod,
    RequirementEvidence,
    RequirementValue,
)
from scione.specifications.review import assess_requirements
from scione.specifications.store import RequirementReview, RequirementReviewSet


def _document() -> IngestedDocument:
    text = "Adhesion per ISO 2409: grade 0 to 1.\nHardness per ISO 15184: 750 g."
    return IngestedDocument(
        document_id="source_1",
        file_name="source.pdf",
        sha256="a" * 64,
        page_count=1,
        pages=[
            DocumentPage(
                page_number=1,
                text=text,
                character_count=len(text),
                non_whitespace_character_count=len("".join(text.split())),
            )
        ],
        text_reading_order=TextReadingOrder.CONTENT_STREAM,
        extraction_status=TextExtractionStatus.READY,
    )


def _requirement(identifier: str = "req_001") -> CustomerRequirement:
    return CustomerRequirement(
        id=identifier,
        original_test_name="Adhesion",
        requested_methods=[RequestedMethod(code="ISO 2409")],
        acceptance_criteria=[RequirementValue(name="Grade", raw_value="0 to 1")],
        evidence=[RequirementEvidence(page=1, quote="Adhesion per ISO 2409: grade 0 to 1.")],
    )


def _run(
    *, rows: list[CustomerRequirement] | None = None, issues: list[str] | None = None
) -> CustomerExtractionRun:
    return CustomerExtractionRun(
        run_id="run_1",
        started_at=datetime.now(UTC),
        document_id="source_1",
        prompt_version="test_prompt",
        schema_version="0.1",
        provider="static",
        model="fixture",
        latency_ms=0,
        usage=TokenUsage(),
        raw_response="{}",
        extraction=CustomerExtraction(
            schema_version="0.1",
            document=CustomerDocumentMetadata(),
            requirements=rows if rows is not None else [_requirement()],
            review_issues=issues or [],
        ),
    )


def _review(status: str) -> RequirementReviewSet:
    # model_copy also allows the triage contract for a future exclusion status to
    # be tested before the persistence model is extended by the UI integration.
    decision = RequirementReview(
        requirement_id="req_001", status="confirmed", updated_at=datetime.now(UTC)
    ).model_copy(update={"status": status})
    return RequirementReviewSet(
        document_id="source_1", run_id="run_1", decisions={"req_001": decision}
    )


def test_ready_is_not_a_confidence_score_or_a_completeness_claim() -> None:
    run = _run()
    assessment = assess_requirements(run, _document())["req_001"]

    assert assessment.state == "ready"
    assert assessment.reasons == ()
    assert not hasattr(assessment, "confidence")
    # The row can be ready while the document still has uncovered references.
    assert coverage_issues(run.extraction, _document())


@pytest.mark.parametrize(
    ("quote", "page", "reason"),
    [
        ("Adhesion | ISO 2409 | grade 0 to 1", 1, "quoted text was not found"),
        ("Adhesion per ISO 2409: grade 0 to 1.", 2, "not in the source document"),
    ],
)
def test_invalid_source_is_actionable_even_if_not_in_saved_model_issues(
    quote: str, page: int, reason: str
) -> None:
    row = _requirement()
    row.evidence = [RequirementEvidence(page=page, quote=quote)]
    assessed = assess_requirements(_run(rows=[row]), _document())[row.id]

    assert assessed.state == "needs_attention"
    assert any(reason in explanation for explanation in assessed.reasons)


def test_whitespace_wrapping_differences_are_not_a_flag() -> None:
    row = _requirement()
    row.evidence[0].quote = "Adhesion per ISO 2409:\n grade 0 to 1."
    assert assess_requirements(_run(rows=[row]), _document())[row.id].state == "ready"


def test_source_validation_is_scoped_to_its_row_and_not_duplicated() -> None:
    invalid = _requirement("req_001")
    invalid.evidence[0].quote = "Invented quotation"
    valid = _requirement("req_002")
    run = _run(rows=[invalid, valid])
    first = assess_requirements(run, _document())
    run.extraction.review_issues = list(first[invalid.id].reasons)
    second = assess_requirements(run, _document())

    assert second[invalid.id].state == "needs_attention"
    assert len(second[invalid.id].reasons) == 1
    assert second[valid.id].state == "ready"


@pytest.mark.parametrize(
    ("issue", "expected"),
    [
        ("req_001 has ambiguous criteria.", {"req_001"}),
        ("Check req_001, req_002: shared conditions may be missing.", {"req_001", "req_002"}),
        ("Only req_0010 has unclear applicability.", {"req_0010"}),
        ("Some conditions may have been omitted.", {"req_001", "req_002", "req_0010"}),
        ("req_missing has unclear applicability.", {"req_001", "req_002", "req_0010"}),
    ],
)
def test_model_issues_are_conservatively_scoped(issue: str, expected: set[str]) -> None:
    run = _run(
        rows=[_requirement("req_001"), _requirement("req_002"), _requirement("req_0010")],
        issues=[issue],
    )
    actual = assess_requirements(run, _document())
    assert {identifier for identifier, item in actual.items() if item.reasons} == expected


def test_empty_method_is_actionable_not_an_inferred_method() -> None:
    row = _requirement()
    row.requested_methods = []
    item = assess_requirements(_run(rows=[row]), _document())[row.id]
    assert item.state == "needs_attention"
    assert item.reasons == ("No test method was identified; check the source.",)


@pytest.mark.parametrize("value_list", ["conditions", "acceptance_criteria"])
def test_one_kind_of_requirement_value_is_enough_for_no_empty_value_flag(value_list: str) -> None:
    row = _requirement()
    row.acceptance_criteria = []
    setattr(row, value_list, [RequirementValue(name="Source detail", raw_value="0 to 1")])
    assert assess_requirements(_run(rows=[row]), _document())[row.id].state == "ready"


def test_both_value_lists_empty_requires_review() -> None:
    row = _requirement()
    row.acceptance_criteria = []
    item = assess_requirements(_run(rows=[row]), _document())[row.id]
    assert item.state == "needs_attention"
    assert any(
        "No conditions or acceptance criteria" in explanation for explanation in item.reasons
    )


@pytest.mark.parametrize(
    ("decision", "state"),
    [
        ("confirmed", "confirmed"),
        ("needs_review", "needs_attention"),
        ("excluded", "excluded"),
        ("rejected", "excluded"),
    ],
)
def test_human_decision_determines_state_without_erasing_automatic_reasons(
    decision: str, state: str
) -> None:
    run = _run(issues=["Check the incomplete extraction."])
    assessed = assess_requirements(run, _document(), _review(decision))["req_001"]
    assert assessed.state == state
    assert "Check the incomplete extraction." in assessed.reasons
    assert run.extraction.review_issues == ["Check the incomplete extraction."]


def test_human_request_to_review_an_otherwise_ready_row_is_actionable() -> None:
    assessed = assess_requirements(_run(), _document(), _review("needs_review"))["req_001"]
    assert assessed.state == "needs_attention"
    assert assessed.reasons == ("Marked for review by a user.",)


def test_review_is_bound_to_the_same_run_and_document() -> None:
    run = _run()
    review = _review("confirmed")
    review.run_id = "other_run"
    with pytest.raises(ValueError, match="different extraction run"):
        assess_requirements(run, _document(), review)
    other_document = _document().model_copy(update={"document_id": "other_source"})
    with pytest.raises(ValueError, match="source document do not match"):
        assess_requirements(run, other_document)


def test_no_rows_yields_no_invented_assessments() -> None:
    assert assess_requirements(_run(rows=[]), _document()) == {}


def _pdf_document() -> tuple[bytes, IngestedDocument]:
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for index, value in enumerate(
            ("Adhesion", "ISO 2409", "grade 0 to 1", "Temperature -20 C")
        ):
            page.insert_text((50, 50 + 20 * index), value)
        text = page.get_text()
        data = pdf.tobytes()
    document = _document().model_copy(
        update={
            "sha256": hashlib.sha256(data).hexdigest(),
            "pages": [
                DocumentPage(
                    page_number=1,
                    text=text,
                    character_count=len(text),
                    non_whitespace_character_count=len("".join(text.split())),
                )
            ],
        }
    )
    return data, document


def test_uniquely_located_table_cells_resolve_only_formatting_flag_and_keep_raw_issues() -> None:
    pdf, document = _pdf_document()
    row = _requirement()
    row.evidence[0].quote = "Adhesion | ISO 2409 | grade 0 to 1"
    run = _run(rows=[row])
    issues = validate_customer_sources(run.extraction, document)
    assert len(issues) == 1
    run.extraction.review_issues.extend(issues)
    unchanged_json = run.model_dump_json()

    assert assess_requirements(run, document)[row.id].state == "needs_attention"
    assessed = assess_requirements(run, document, pdf_bytes=pdf)[row.id]

    assert assessed.state == "ready"
    assert assessed.reasons == ()
    assert run.model_dump_json() == unchanged_json


@pytest.mark.parametrize(
    "quote",
    [
        "Adhesion | ISO 2409 | grade 0 to 2",
        "Adhesion | ISO 2409 | Temperature 20 C",
        "Wholly invented citation",
    ],
)
def test_partial_or_unmatched_pdf_quote_stays_actionable(quote: str) -> None:
    pdf, document = _pdf_document()
    row = _requirement()
    row.evidence[0].quote = quote
    assessment = assess_requirements(_run(rows=[row]), document, pdf_bytes=pdf)[row.id]
    assert assessment.state == "needs_attention"
    assert any("quoted text was not found" in reason for reason in assessment.reasons)


def test_located_pdf_quote_does_not_suppress_independent_model_issue() -> None:
    pdf, document = _pdf_document()
    row = _requirement()
    row.evidence[0].quote = "Adhesion | ISO 2409 | grade 0 to 1"
    model_issue = "req_001: applicability to this substrate is unclear."
    run = _run(rows=[row], issues=[model_issue])
    run.extraction.review_issues.extend(validate_customer_sources(run.extraction, document))

    assessment = assess_requirements(run, document, pdf_bytes=pdf)[row.id]

    assert assessment.state == "needs_attention"
    assert assessment.reasons == (model_issue,)


def test_ambiguous_table_cell_is_not_treated_as_a_fully_located_quote() -> None:
    pdf, document = _pdf_document()
    with pymupdf.open(stream=pdf, filetype="pdf") as duplicate:
        duplicate[0].insert_text((50, 150), "ISO 2409")
        pdf = duplicate.tobytes()
        text = duplicate[0].get_text()
    document.sha256 = hashlib.sha256(pdf).hexdigest()
    document.pages[0].text = text
    document.pages[0].character_count = len(text)
    document.pages[0].non_whitespace_character_count = len("".join(text.split()))
    row = _requirement()
    row.evidence[0].quote = "Adhesion | ISO 2409 | grade 0 to 1"

    assessment = assess_requirements(_run(rows=[row]), document, pdf_bytes=pdf)[row.id]

    assert assessment.state == "needs_attention"
    assert any("quoted text was not found" in reason for reason in assessment.reasons)


def test_a_different_pdf_cannot_be_used_to_clear_source_warnings() -> None:
    pdf, document = _pdf_document()
    with pytest.raises(ValueError, match="PDF bytes do not match"):
        assess_requirements(_run(), document, pdf_bytes=pdf + b"different bytes")


def test_pdf_location_failure_is_not_treated_as_validation_success(monkeypatch) -> None:
    from scione.specifications import review
    from scione.specifications.pdf_preview import PDFPreviewError

    pdf, document = _pdf_document()
    row = _requirement()
    row.evidence[0].quote = "Adhesion | ISO 2409 | grade 0 to 1"

    def unavailable_locator(*args, **kwargs):
        raise PDFPreviewError("Cannot inspect this page.")

    monkeypatch.setattr(review, "inspect_pdf_highlights", unavailable_locator)
    assessment = assess_requirements(_run(rows=[row]), document, pdf_bytes=pdf)[row.id]

    assert assessment.state == "needs_attention"
    assert any("quoted text was not found" in reason for reason in assessment.reasons)


def _table_document() -> tuple[bytes, IngestedDocument]:
    rows = [
        ("Test", "Method", "Criterion", "Section"),
        ("Adhesion", "ISO 2409", "GT <= 1", "3.2"),
        ("Hardness", "ISO 15184", "GT <= 1", "3.2"),
    ]
    xs = (30, 180, 310, 450, 550)
    ys = (40, 80, 120, 160)
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=400)
        for x in xs:
            page.draw_line((x, ys[0]), (x, ys[-1]))
        for y in ys:
            page.draw_line((xs[0], y), (xs[-1], y))
        # Native text is column-ordered, but the ruled row still anchors cells.
        for col in range(4):
            for row in range(len(rows)):
                page.insert_text((xs[col] + 5, ys[row] + 25), rows[row][col])
        text = page.get_text()
        data = pdf.tobytes()
    document = _document().model_copy(
        update={
            "sha256": hashlib.sha256(data).hexdigest(),
            "pages": [
                DocumentPage(
                    page_number=1,
                    text=text,
                    character_count=len(text),
                    non_whitespace_character_count=len("".join(text.split())),
                )
            ],
        }
    )
    return data, document


def test_repeated_cells_within_one_exact_table_row_are_not_false_review_flags() -> None:
    pdf, document = _table_document()
    row = _requirement()
    row.evidence[0].quote = "Adhesion | ISO 2409 | GT <= 1 | 3.2"
    run = _run(rows=[row])
    run.extraction.review_issues.extend(validate_customer_sources(run.extraction, document))
    unchanged_run = run.model_dump_json()

    assert assess_requirements(run, document)[row.id].state == "needs_attention"
    assert assess_requirements(run, document, pdf_bytes=pdf)[row.id].state == "ready"
    assert run.model_dump_json() == unchanged_run


@pytest.mark.parametrize(
    ("quote", "page"),
    [
        ("Adhesion | ISO 15184 | GT <= 1 | 3.2", 1),
        ("Adhesion | ISO 2409 | GT <= 2 | 3.2", 1),
        ("Adhesion | ISO 2409 | GT >= 1 | 3.2", 1),
        ("Adhesion | ISO 2409 | GT <= 1 | 3.2", 2),
    ],
)
def test_table_row_clearance_never_hides_wrong_values_methods_or_pages(
    quote: str, page: int
) -> None:
    pdf, document = _table_document()
    row = _requirement()
    row.evidence = [RequirementEvidence(page=page, quote=quote)]

    assessed = assess_requirements(_run(rows=[row]), document, pdf_bytes=pdf)[row.id]
    assert assessed.state == "needs_attention"
    assert len(assessed.reasons) == 1


def test_valid_table_row_cannot_clear_an_independent_second_citation() -> None:
    pdf, document = _table_document()
    row = _requirement()
    row.evidence = [
        RequirementEvidence(page=1, quote="Adhesion | ISO 2409 | GT <= 1 | 3.2"),
        RequirementEvidence(page=1, quote="Hardness | ISO 15184 | GT <= 0 | 3.2"),
    ]

    assessed = assess_requirements(_run(rows=[row]), document, pdf_bytes=pdf)[row.id]
    assert assessed.state == "needs_attention"
    assert len(assessed.reasons) == 1
    assert "evidence 2" in assessed.reasons[0]


def test_exact_table_row_does_not_clear_model_caution_or_explicit_human_review() -> None:
    pdf, document = _table_document()
    row = _requirement()
    row.evidence[0].quote = "Adhesion | ISO 2409 | GT <= 1 | 3.2"
    issue = "req_001: model could not determine applicability to the selected panels."
    run = _run(rows=[row], issues=[issue])

    assessed = assess_requirements(run, document, pdf_bytes=pdf)[row.id]
    assert assessed.state == "needs_attention"
    assert assessed.reasons == (issue,)
    assessed = assess_requirements(
        _run(rows=[row]), document, _review("needs_review"), pdf_bytes=pdf
    )[row.id]
    assert assessed.state == "needs_attention"
    assert assessed.reasons == ("Marked for review by a user.",)


def _customer_procedure_document(*, method: str | None = None) -> tuple[bytes, IngestedDocument]:
    procedure = "500 h at 90 C"
    if method:
        procedure = method + "; " + procedure
    rows = [
        ("Test", "Customer procedure", "Acceptance"),
        ("Heat aging", procedure, "No cracking"),
        ("Other procedure", "100 h at 20 C", "No discoloration"),
    ]
    xs, ys = (30, 190, 440, 650), (40, 85, 130, 175)
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=700, height=400)
        for x in xs:
            page.draw_line((x, ys[0]), (x, ys[-1]))
        for y in ys:
            page.draw_line((xs[0], y), (xs[-1], y))
        for col in range(3):
            for row in range(len(rows)):
                page.insert_text((xs[col] + 5, ys[row] + 25), rows[row][col], fontsize=10)
        text, data = page.get_text(), pdf.tobytes()
    document = _document().model_copy(
        update={
            "sha256": hashlib.sha256(data).hexdigest(),
            "pages": [
                DocumentPage(
                    page_number=1,
                    text=text,
                    character_count=len(text),
                    non_whitespace_character_count=len("".join(text.split())),
                )
            ],
        }
    )
    return data, document


def _customer_procedure_row(quote: str | None = None) -> CustomerRequirement:
    return CustomerRequirement(
        id="req_001",
        original_test_name="Heat aging",
        conditions=[RequirementValue(name="Customer procedure", raw_value="500 h at 90 C")],
        acceptance_criteria=[RequirementValue(name="Appearance", raw_value="No cracking")],
        applicability=["4.1 Humidity, corrosion and thermal"],
        evidence=[
            RequirementEvidence(
                page=1,
                quote=quote if quote is not None else "Heat aging | 500 h at 90 C | No cracking",
            )
        ],
    )


def test_complete_exact_source_defined_procedure_waives_only_missing_formal_method() -> None:
    pdf, document = _customer_procedure_document()
    row = _customer_procedure_row()
    run = _run(rows=[row])
    original = run.model_dump_json()

    assert assess_requirements(run, document)[row.id].state == "needs_attention"
    assessed = assess_requirements(run, document, pdf_bytes=pdf)[row.id]
    assert assessed.state == "ready"
    assert assessed.reasons == ()
    assert row.requested_methods == []
    assert run.model_dump_json() == original


@pytest.mark.parametrize(
    "quote",
    [
        "Heat aging | 500 h at 95 C | No cracking",
        "Heat aging | 100 h at 20 C | No cracking",
        "Heat aging",
        "Heat aging | 500 h at 90 C",
    ],
)
def test_invented_cross_row_or_partial_procedure_quote_does_not_waive_missing_method(quote) -> None:
    pdf, document = _customer_procedure_document()
    row = _customer_procedure_row(quote)
    assessed = assess_requirements(_run(rows=[row]), document, pdf_bytes=pdf)[row.id]
    assert assessed.state == "needs_attention"
    assert "No test method was identified; check the source." in assessed.reasons


@pytest.mark.parametrize(
    "quote",
    [
        "Heat aging | ISO 11341; 500 h at 90 C | No cracking",
        "Heat aging | 500 h at 90 C | No cracking",
        "Heat aging",
    ],
)
def test_source_defined_name_never_waives_an_omitted_formal_method_in_source_row(quote) -> None:
    pdf, document = _customer_procedure_document(method="ISO 11341")
    row = _customer_procedure_row(quote)
    assessed = assess_requirements(_run(rows=[row]), document, pdf_bytes=pdf)[row.id]
    assert assessed.state == "needs_attention"
    assert "No test method was identified; check the source." in assessed.reasons


def test_proved_customer_procedure_keeps_model_caution_and_explicit_human_review() -> None:
    pdf, document = _customer_procedure_document()
    row = _customer_procedure_row()
    issue = "req_001: model could not resolve this customer's material qualification."
    assessed = assess_requirements(_run(rows=[row], issues=[issue]), document, pdf_bytes=pdf)[
        row.id
    ]
    assert assessed.state == "needs_attention"
    assert assessed.reasons == (issue,)
    reviewed = assess_requirements(
        _run(rows=[row]), document, _review("needs_review"), pdf_bytes=pdf
    )[row.id]
    assert reviewed.state == "needs_attention"
    assert reviewed.reasons == ("Marked for review by a user.",)


@pytest.mark.parametrize("cause", ["unknown_name", "no_conditions", "wrong_page"])
def test_source_procedure_clearance_requires_curated_name_conditions_and_correct_page(
    cause,
) -> None:
    pdf, document = _customer_procedure_document()
    row = _customer_procedure_row()
    if cause == "unknown_name":
        row.original_test_name = "Customer invented arbitrary heat operation"
    if cause == "no_conditions":
        row.conditions = []
    if cause == "wrong_page":
        row.evidence[0].page = 2
    assessed = assess_requirements(_run(rows=[row]), document, pdf_bytes=pdf)[row.id]
    assert assessed.state == "needs_attention"
    assert "No test method was identified; check the source." in assessed.reasons


def test_procedure_locator_failure_cannot_clear_missing_method(monkeypatch) -> None:
    from scione.specifications import review
    from scione.specifications.pdf_preview import PDFPreviewError

    pdf, document = _customer_procedure_document()
    row = _customer_procedure_row()

    def unavailable(*args, **kwargs):
        raise PDFPreviewError("Independent source inspection unavailable.")

    monkeypatch.setattr(review, "inspect_pdf_highlights", unavailable)
    assessed = assess_requirements(_run(rows=[row]), document, pdf_bytes=pdf)[row.id]
    assert assessed.state == "needs_attention"
    assert "No test method was identified; check the source." in assessed.reasons
