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
from scione.specifications.review import (
    assess_requirements,
    document_review_issues,
    is_provenance_notice,
)
from scione.specifications.review_audits import scoped_specimen_review_reasons
from scione.specifications.store import RequirementReview, RequirementReviewSet
from scione.structured.models import AtomicClaim


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


def _two_method_rows() -> list[CustomerRequirement]:
    other = _requirement("req_002")
    other.original_test_name = "Hardness"
    other.requested_methods = [RequestedMethod(code="ISO 15184")]
    other.evidence = [RequirementEvidence(page=1, quote="Hardness per ISO 15184: 750 g.")]
    return [_requirement(), other]


def test_unnumbered_literal_method_issue_only_flags_its_owning_row() -> None:
    issue = "The acceptance class for ISO 2409 is not stated; confirm it with the customer."
    run = _run(rows=_two_method_rows(), issues=[issue])
    result = assess_requirements(run, _document())
    assert result["req_001"].state == "needs_attention"
    assert result["req_002"].state == "ready"
    assert run.extraction.review_issues == [issue]


@pytest.mark.parametrize("issue", [
    "ISO 2409 and ISO 15184 have inconsistent specimen preparation.",
    "ISO 2409 and ISO 99999:2025 have an unclear relationship.",
    "All tests including ISO 2409 may have missing conditions.",
    "Document revision conflict affects ISO 2409 and the remaining tests.",
])
def test_cross_test_unknown_reference_or_global_conflict_does_not_get_under_routed(issue):
    result = assess_requirements(_run(rows=_two_method_rows(), issues=[issue]), _document())
    assert {key for key, item in result.items() if item.state == "needs_attention"} == {
        "req_001", "req_002",
    }


def _synthetic_document() -> IngestedDocument:
    source = _document()
    page = source.pages[0]
    text = page.text + "\nAll method IDs are synthetic bank entries."
    source.pages[0] = page.model_copy(update={"text": text})
    return source


@pytest.mark.parametrize("issue", [
    "The document is a synthetic benchmark sample; all cited method designations are "
    "fictional and no equivalence between ISO 2409 and ISO 15184 is declared.",
    "Les identifiants de méthodes cités (ISO 2409, ISO 15184) sont déclarés synthétiques "
    "par le document; aucune équivalence officielle n’est revendiquée.",
    "req_001: 方法编号为 SciOne AI 方法库 synthetic reference，非正式标准。",
    "The source states all method IDs (ISO 2409, ISO 15184) are synthetic and no official "
    "equivalence is claimed; method designations are retained exactly as printed.",
    "El documento indica que los identificadores de métodos citados son entradas sintéticas "
    "del banco y no se afirma equivalencia oficial; los códigos se conservan tal como están "
    "impresos.",
])
def test_exact_source_backed_provenance_notices_do_not_require_row_approval(issue):
    run = _run(rows=_two_method_rows(), issues=[issue])
    source = _synthetic_document()
    assert is_provenance_notice(issue, source)
    assert all(item.state == "ready" for item in assess_requirements(run, source).values())
    assert run.extraction.review_issues == [issue]


@pytest.mark.parametrize("suffix", [
    " The test conditions are ambiguous.",
    " Two requirements are missing.",
    " Confirm the correct acceptance class.",
    " 温度的单位不清楚。",
])
def test_provenance_mixed_with_real_concerns_remains_actionable(suffix):
    issue = "方法编号为 SciOne AI 方法库 synthetic reference，非正式标准。" + suffix
    assert not is_provenance_notice(issue, _synthetic_document())
    result = assess_requirements(
        _run(rows=_two_method_rows(), issues=[issue]), _synthetic_document()
    )
    assert all(item.state == "needs_attention" for item in result.values())


def test_provenance_notice_not_proved_by_source_remains_actionable():
    issue = "方法编号为 SciOne AI 方法库 synthetic reference，非正式标准。"
    assert not is_provenance_notice(issue, _document())
    result = assess_requirements(_run(issues=[issue]), _document())["req_001"]
    assert result.state == "needs_attention"


def test_exact_curated_property_phrase_routes_an_unnumbered_concern_to_its_row():
    rows = _two_method_rows()
    rows[0].original_test_name = "Master-panel colour difference"
    issue = "Colour-difference unit symbol should be checked against the PDF."
    result = assess_requirements(_run(rows=rows, issues=[issue]), _document())
    assert result["req_001"].state == "needs_attention"
    assert result["req_002"].state == "ready"


@pytest.mark.parametrize("issue", [
    "Several tests including colour difference may have missing requirements.",
    "Colour difference method ISO 99999:2025 has an unclear relationship.",
    "Document revision affects colour difference and remaining tests.",
])
def test_property_phrase_cannot_hide_global_or_unknown_method_concerns(issue):
    rows = _two_method_rows()
    rows[0].original_test_name = "Master-panel colour difference"
    result = assess_requirements(_run(rows=rows, issues=[issue]), _document())
    assert all(item.state == "needs_attention" for item in result.values())


def _scope_fixture(clause, *, atom_fragment=None):
    rows = _two_method_rows()
    source = _document()
    source.pages[0].text += "\n" + clause
    for row in rows:
        row.conditions = [RequirementValue(name="Conditioning", raw_value=clause)]
        row.evidence.append(RequirementEvidence(page=1, quote=clause))
        row.atomic_claims = [AtomicClaim(
            id="native_duration", parameter_id="duration", label="Duration", kind="condition",
            stage="conditioning", operator="=", value=48, unit="h", source_pages=[1],
            raw_text=atom_fragment or clause,
        )]
    return rows, source


def test_test_specific_specimens_cannot_silently_make_another_test_ready():
    rows, source = _scope_fixture("Condition five adhesion panels for 48 h.")
    issues = scoped_specimen_review_reasons(rows, source)
    assert set(issues) == {"req_002"}
    result = assess_requirements(_run(rows=rows), source)
    assert result["req_001"].state == "ready"
    assert result["req_002"].state == "needs_attention"
    assert "another test's specimens" in result["req_002"].reasons[0]


@pytest.mark.parametrize("clause", [
    "Condition all panels, including adhesion panels, for 48 h.",
    "Condition adhesion panels and hardness panels for 48 h.",
    "Condition five painted panels for 48 h.",
])
def test_universal_joint_or_unidentified_specimens_do_not_receive_invented_owners(clause):
    rows, source = _scope_fixture(clause)
    assert scoped_specimen_review_reasons(rows, source) == {}


def test_anaphoric_other_specimens_keep_their_explicit_source_sentence_owner():
    clause = "Condition five adhesion panels for 48 h; hold the other five for 48 h."
    rows, source = _scope_fixture(clause, atom_fragment="hold the other five for 48 h")
    assert set(scoped_specimen_review_reasons(rows, source)) == {"req_002"}


@pytest.mark.parametrize("parameter,unit,raw", [
    ("specimen_length", "mm", "Specimen thickness: 3.2 mm."),
    ("impact_energy", "kJ/m²", "Impact response: 3.2 kJ/m²."),
    ("temperature", "°C/min", "Heating rate: 3.2 °C/min."),
])
def test_known_native_scientific_conflicts_flag_only_the_affected_row(parameter, unit, raw):
    rows = _two_method_rows()
    source = _document()
    source.pages[0].text += "\n" + raw
    rows[0].conditions = [RequirementValue(name="Condition", raw_value=raw)]
    rows[0].evidence.append(RequirementEvidence(page=1, quote=raw))
    rows[0].atomic_claims = [AtomicClaim(
        id="native_wrong_quantity", parameter_id=parameter, label=parameter,
        kind="condition", operator="=", value="3.2", unit=unit,
        raw_text=raw, source_pages=[1],
    )]
    result = assess_requirements(_run(rows=rows), source)
    assert result["req_001"].state == "needs_attention"
    assert any("different quantity" in reason for reason in result["req_001"].reasons)
    assert result["req_002"].state == "ready"
    assert rows[0].atomic_claims[0].parameter_id == parameter


def test_known_source_code_mismatch_is_actionable_without_using_confidence_scores():
    rows = _two_method_rows()
    source = _document()
    raw = "Use D65 illuminant."
    source.pages[0].text += "\n" + raw
    rows[0].conditions = [RequirementValue(name="Illuminant", raw_value=raw)]
    rows[0].evidence.append(RequirementEvidence(page=1, quote=raw))
    rows[0].atomic_claims = [AtomicClaim(
        id="native_code", parameter_id="illuminant", label="Illuminant", kind="condition",
        operator="text", text_value="D50", raw_text=raw, source_pages=[1],
    )]
    result = assess_requirements(_run(rows=rows), source)
    assert result["req_001"].state == "needs_attention"
    assert result["req_002"].state == "ready"


def test_unfamiliar_units_and_unresolved_count_parsing_do_not_blanket_require_approval():
    rows = _two_method_rows()
    source = _document()
    raw = "Record two lots and an unfamiliar reading of 37 bananas."
    source.pages[0].text += "\n" + raw
    rows[0].conditions = [RequirementValue(name="Condition", raw_value=raw)]
    rows[0].evidence.append(RequirementEvidence(page=1, quote=raw))
    rows[0].atomic_claims = [
        AtomicClaim(
            id="unfamiliar", parameter_id="unfamiliar_reading", label="Reading",
            kind="condition", operator="=", value=37, unit="bananas",
            raw_text=raw, source_pages=[1],
        ),
        AtomicClaim(
            id="count", parameter_id="specimen_count", label="Count", kind="condition",
            operator="=", value=2, unit="lots", raw_text=raw, source_pages=[1],
        ),
    ]
    result = assess_requirements(_run(rows=rows), source)
    assert all(item.state == "ready" for item in result.values())


def test_source_proved_revision_conflict_is_one_document_question_not_each_test_error():
    rows = _two_method_rows()
    source = _document()
    source.pages[0].text += "\nSPEC-218 | REV A\nRevision / date B / 19 October 2026"
    issue = (
        "Document header states 'SPEC-218 | REV A' while the revision block states "
        "'Revision / date B / 19 October 2026'; the controlling revision is ambiguous "
        "and requires confirmation."
    )
    run = _run(rows=rows, issues=[issue])
    assert document_review_issues(run, source) == (issue,)
    assert all(item.state == "ready" for item in assess_requirements(run, source).values())
    assert run.extraction.review_issues == [issue]


def test_scientific_method_revision_is_not_reclassified_as_document_metadata():
    issue = "Document revision for ISO 2409 is ambiguous; confirm its test conditions."
    run = _run(rows=_two_method_rows(), issues=[issue])
    assert document_review_issues(run, _document()) == ()
    assert assess_requirements(run, _document())["req_001"].state == "needs_attention"


def test_literal_quoted_missing_setpoints_only_flags_the_source_owner():
    rows = _two_method_rows()
    source = _document()
    clause = "Exposure uses the agreed operating set points."
    source.pages[0].text += "\n" + clause
    rows[0].conditions.append(RequirementValue(name="Exposure", raw_value=clause))
    rows[0].evidence.append(RequirementEvidence(page=1, quote=clause))
    issue = "The salt-fog 'agreed operating set points' are not numerically defined."
    result = assess_requirements(_run(rows=rows, issues=[issue]), source)
    assert result["req_001"].state == "needs_attention"
    assert result["req_002"].state == "ready"


def test_global_question_is_not_under_routed_by_a_single_quoted_fragment():
    rows = _two_method_rows()
    source = _document()
    clause = "Exposure uses the agreed operating set points."
    source.pages[0].text += "\n" + clause
    rows[0].evidence.append(RequirementEvidence(page=1, quote=clause))
    issue = "All tests may miss conditions, including 'agreed operating set points'."
    result = assess_requirements(_run(rows=rows, issues=[issue]), source)
    assert all(item.state == "needs_attention" for item in result.values())


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
