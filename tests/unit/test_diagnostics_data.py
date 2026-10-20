"""Developer projections describe saved drafts, never pretend to be accuracy."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pymupdf
import pytest

from scione.providers import TokenUsage
from scione.schemas import DocumentPage, IngestedDocument, TextExtractionStatus, TextReadingOrder
from scione.specifications.models import (
    CustomerDocumentMetadata,
    CustomerExtraction,
    CustomerExtractionRun,
    CustomerRequirement,
    RequestedMethod,
    RequirementValue,
)
from scione.specifications.pdf_preview import PDFPreviewError
from scione.specifications.review import RequirementAssessment
from scione.specifications.store import RequirementReview, RequirementReviewSet
from scione.web.diagnostics_data import (
    comparison_rows,
    evidence_rows,
    requirement_rows,
    run_summary,
)


def _requirement(identifier: str = "req_adhesion", **changes: object) -> CustomerRequirement:
    return CustomerRequirement.model_validate(
        {
            "id": identifier,
            "original_test_name": "Adhesion",
            "requested_methods": [{"code": "ISO 2409"}],
            "conditions": [{"name": "Spacing", "raw_value": "2 mm", "unit": "mm"}],
            "acceptance_criteria": [
                {"name": "Grade", "raw_value": "0 to 1", "context": "steel panels"}
            ],
            "applicability": ["All bake conditions"],
            "special_instructions": ["Retain the coating"],
            "evidence": [{"page": 1, "quote": "Adhesion per ISO 2409: grade 0 to 1."}],
            **changes,
        }
    )


def _run(rows: list[CustomerRequirement] | None = None, **changes: object) -> CustomerExtractionRun:
    return CustomerExtractionRun.model_validate(
        {
            "run_id": "run_one",
            "started_at": datetime(2026, 10, 17, tzinfo=UTC),
            "document_id": "source_one",
            "prompt_version": "fixture_prompt",
            "schema_version": "0.1",
            "provider": "offline",
            "model": "fixture",
            "latency_ms": 1234.5,
            "usage": TokenUsage(),
            "raw_response": "  unchanged provider text\n",
            "extraction": CustomerExtraction(
                schema_version="0.1",
                document=CustomerDocumentMetadata(),
                requirements=rows if rows is not None else [_requirement()],
            ),
            **changes,
        }
    )


def _source() -> tuple[IngestedDocument, bytes]:
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_textbox(
            pymupdf.Rect(40, 40, 550, 400),
            "Adhesion per ISO 2409: grade 0 to 1.\n"
            "Hardness per ISO 15184: 750 g.\nRepeated label\nRepeated label",
        )
        text = page.get_text()
        data = pdf.tobytes()
    return (
        IngestedDocument(
            document_id="source_one",
            file_name="fixture.pdf",
            sha256=hashlib.sha256(data).hexdigest(),
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
        ),
        data,
    )


def _review(**changes: object) -> RequirementReviewSet:
    return RequirementReviewSet.model_validate(
        {
            "document_id": "source_one",
            "run_id": "run_one",
            "decisions": {
                "req_adhesion": RequirementReview(
                    requirement_id="req_adhesion",
                    status="confirmed",
                    updated_at=datetime(2026, 10, 17, tzinfo=UTC),
                )
            },
            **changes,
        }
    )


def test_summary_counts_records_and_method_references_not_unique_canonical_tests() -> None:
    rows = [_requirement(), _requirement("req_other")]
    rows[1].requested_methods.append(RequestedMethod(code="ASTM D3359"))
    run = _run(rows)
    before = run.model_dump_json()

    summary = run_summary(run)

    assert summary["Records"] == 2
    assert summary["Method references"] == 3
    assert summary["Cited pages"] == 1
    assert summary["Calls"] == 1
    assert summary["Latency (s)"] == 1.234
    assert summary["Input tokens"] is None
    assert summary["Output tokens"] is None
    assert summary["Total tokens"] is None
    assert not any("accuracy" in key.casefold() or "recall" in key.casefold() for key in summary)
    assert run.model_dump_json() == before


def test_summary_keeps_provider_reported_usage_and_actual_call_count() -> None:
    run = _run(
        strategy="focused_passes",
        usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=35),
        calls=[
            {
                "call_index": index,
                "focus_pages": [index],
                "provider": "offline",
                "model": "fixture",
                "latency_ms": 1,
                "usage": {},
                "raw_response": "{}",
            }
            for index in (1, 2)
        ],
    )
    summary = run_summary(run)
    assert summary["Strategy"] == "focused_passes"
    assert summary["Calls"] == 2
    assert summary["Input tokens"] == 10
    assert summary["Output tokens"] == 20
    assert summary["Total tokens"] == 35  # Do not silently recompute a provider's totals.


def test_historical_focused_run_without_call_records_does_not_invent_call_count() -> None:
    assert run_summary(_run(strategy="focused_passes"))["Calls"] is None


def test_requirement_rows_keep_values_scope_and_unknown_review_without_mutation() -> None:
    run = _run()
    before = run.model_dump_json()

    row = requirement_rows(run)[0]

    assert row["ID"] == "req_adhesion"
    assert row["Methods"] == "ISO 2409"
    assert row["Conditions"] == "Spacing: 2 mm (unit: mm)"
    assert row["Criteria"] == "Grade: 0 to 1 [steel panels]"
    assert row["Scope"] == "All bake conditions"
    assert row["Instructions"] == "Retain the coating"
    assert row["Source pages"] == "1"
    assert row["Review state"] == "Not assessed"
    assert row["Review flags"] == ""
    assert run.model_dump_json() == before


def test_assessment_and_human_decision_stay_distinct_and_reasons_remain_visible() -> None:
    assessments = {"req_adhesion": RequirementAssessment("needs_attention", ("Check source.",))}
    review = _review()
    original = review.model_dump_json()
    assert requirement_rows(_run(), assessments)[0]["Review state"] == "Needs attention"
    rows = requirement_rows(_run(), assessments, review)
    assert rows[0]["Review state"] == "Confirmed"
    assert rows[0]["Review flags"] == "Check source."
    assert review.model_dump_json() == original
    assert (
        requirement_rows(_run(), {"req_adhesion": RequirementAssessment("ready")})[0][
            "Review state"
        ]
        == "No row flags"
    )


@pytest.mark.parametrize("status", ["confirmed", "excluded", "needs_review"])
def test_explicit_review_states_are_visible_without_requiring_assessments(status: str) -> None:
    review = _review()
    review.decisions["req_adhesion"].status = status
    expected = {
        "confirmed": "Confirmed",
        "excluded": "Excluded",
        "needs_review": "Needs attention",
    }
    assert requirement_rows(_run(), review=review)[0]["Review state"] == expected[status]


@pytest.mark.parametrize("changes", [{"document_id": "other"}, {"run_id": "other"}])
def test_requirement_rows_reject_review_from_another_document_or_run(changes: dict) -> None:
    with pytest.raises(ValueError, match="different extraction"):
        requirement_rows(_run(), review=_review(**changes))


def test_evidence_distinguishes_text_validation_pdf_location_and_ambiguity() -> None:
    document, data = _source()
    run = _run(
        [
            _requirement(),
            _requirement("req_format", evidence=[{"page": 1, "quote": "ADHESION | ISO 2409"}]),
            _requirement("req_missing", evidence=[{"page": 1, "quote": "A made-up source"}]),
            _requirement("req_page", evidence=[{"page": 2, "quote": "Adhesion"}]),
            _requirement("req_ambiguous", evidence=[{"page": 1, "quote": "Repeated label"}]),
            _requirement("req_partial", evidence=[{"page": 1, "quote": "Adhesion | absent"}]),
        ]
    )
    before = run.model_dump_json(), document.model_dump_json(), data

    rows = evidence_rows(run, document, data)

    assert [(row["Text check"], row["Highlight"]) for row in rows] == [
        ("Found", "Located"),
        # Both snippets exist, but the omitted "per" means this is neither a
        # continuous quotation nor a reconstructed complete source-table row.
        ("Not found", "Partly located"),
        ("Not found", "Not located"),
        ("Invalid page", "Invalid page"),
        ("Found", "Ambiguous"),
        ("Not found", "Partly located"),
    ]
    assert rows[0]["Page"] == 1
    assert rows[0]["Quote"] == run.extraction.requirements[0].evidence[0].quote
    assert (run.model_dump_json(), document.model_dump_json(), data) == before


def test_evidence_without_pdf_does_not_invent_highlight_results() -> None:
    document, _ = _source()
    assert evidence_rows(_run(), document)[0]["Text check"] == "Found"
    assert evidence_rows(_run(), document)[0]["Highlight"] == "Not checked"


def test_evidence_pdf_locator_error_is_visible_not_a_good_match(monkeypatch) -> None:
    document, data = _source()

    def fail_locator(*args, **kwargs):
        raise PDFPreviewError("Preview unavailable")

    monkeypatch.setattr("scione.web.diagnostics_data.inspect_pdf_highlights", fail_locator)
    assert evidence_rows(_run(), document, data)[0]["Highlight"] == "Unavailable"


def test_evidence_rejects_wrong_document_or_pdf_bytes() -> None:
    document, data = _source()
    with pytest.raises(ValueError, match="do not match"):
        evidence_rows(_run(document_id="other"), document, data)
    with pytest.raises(ValueError, match="PDF bytes"):
        evidence_rows(_run(), document, b"wrong PDF")


def test_comparison_ignores_run_local_ids_but_reports_changed_raw_fields() -> None:
    baseline = _run()
    current = _run([_requirement("req_other")], run_id="run_two")
    before = baseline.model_dump_json(), current.model_dump_json()
    row = comparison_rows(baseline, current)[0]
    assert row["Change"] == "Unchanged"
    assert row["Baseline IDs"] == "req_adhesion"
    assert row["Selected IDs"] == "req_other"
    assert (baseline.model_dump_json(), current.model_dump_json()) == before

    current.extraction.requirements[0].acceptance_criteria = [
        RequirementValue(name="Grade", raw_value="2")
    ]
    row = comparison_rows(baseline, current)[0]
    assert row["Change"] == "Changed"
    assert row["Detail"] == "acceptance_criteria"


def test_comparison_does_not_link_synonyms_equivalent_methods_or_same_ids() -> None:
    baseline = _run()
    renamed = _run([_requirement(original_test_name="Cross-cut adhesion")], run_id="run_two")
    assert [row["Change"] for row in comparison_rows(baseline, renamed)] == [
        "Baseline only",
        "Selected only",
    ]
    other_method = _run(
        [_requirement(requested_methods=[{"code": "ASTM D3359"}])], run_id="run_three"
    )
    assert [row["Change"] for row in comparison_rows(baseline, other_method)] == [
        "Baseline only",
        "Selected only",
    ]


def test_comparison_normalizes_case_and_whitespace_only_and_flags_duplicate_keys() -> None:
    baseline = _run()
    formatted = _run(
        [_requirement(original_test_name="ADHESION", requested_methods=[{"code": "iso  2409"}])]
    )
    rows = comparison_rows(baseline, formatted)
    assert len(rows) == 1
    assert rows[0]["Change"] == "Changed"  # Linked, but raw model fields still differ.
    assert "original_test_name" in rows[0]["Detail"]
    assert "requested_methods" in rows[0]["Detail"]
    repeated = _run([_requirement(), _requirement("req_second")])
    row = comparison_rows(baseline, repeated)[0]
    assert row["Change"] == "Ambiguous"
    assert row["Selected IDs"] == "req_adhesion, req_second"
    assert "no automatic row pairing" in row["Detail"]


def test_comparison_rejects_other_source_document_and_handles_empty_runs() -> None:
    assert comparison_rows(_run([]), _run([])) == []
    with pytest.raises(ValueError, match="same source"):
        comparison_rows(_run(), _run(document_id="other"))
