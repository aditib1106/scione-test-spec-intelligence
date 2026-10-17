"""Lab-facing PDFs stay concise without losing requirements or inventing approvals."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal

import pymupdf
import pytest

from scione.catalogs.models import LabCapability
from scione.catalogs.seed import default_demo_lab, load_reference_catalog
from scione.providers import TokenUsage
from scione.reports.matching import lab_fingerprint
from scione.reports.models import (
    GapLine,
    MappingChoice,
    QuoteLine,
    ReportSnapshot,
    ReportWorkspace,
)
from scione.reports.pdf import ReportPDFError, render_report_pdf
from scione.specifications.models import CustomerExtraction, CustomerExtractionRun
from scione.specifications.store import RequirementReview, RequirementReviewSet


def _snapshot(*, count: int = 6) -> ReportSnapshot:
    document_id = "a" * 16
    requirements = [
        {
            "id": f"req_pdf_{index}",
            "original_test_name": f"Coating check [{index:03d}]",
            "requested_methods": [
                {"code": "ISO 2409", "revision": "2020", "role": "Required method"}
            ],
            "conditions": [
                {
                    "name": "Spacing",
                    "raw_value": "2 mm",
                    "unit": "mm",
                    "context": "All coated panels",
                }
            ],
            "acceptance_criteria": [
                {"name": "Rating", "raw_value": "GT 0 to 1", "context": "After conditioning"}
            ],
            "applicability": ["Both steel and aluminum"],
            "special_instructions": ["Do not substitute methods"],
            "evidence": [{"page": index + 1, "section": "3.2", "quote": f"SOURCE ROW {index}"}],
        }
        for index in range(count)
    ]
    run = CustomerExtractionRun(
        run_id="pdf_run_one",
        started_at=datetime(2026, 10, 17, tzinfo=UTC),
        document_id=document_id,
        prompt_version="customer_fixture",
        schema_version="0.1",
        provider="MODEL_PROVIDER_SENTINEL",
        model="MODEL_NAME_SENTINEL",
        latency_ms=10,
        usage=TokenUsage(),
        raw_response="RAW_RESPONSE_SENTINEL must not become report prose",
        extraction=CustomerExtraction.model_validate(
            {
                "schema_version": "0.1",
                "document": {
                    "title": "Customer coating specification",
                    "identifier": "SPEC-PDF-01",
                    "revision": "Rev C",
                },
                "requirements": requirements,
            }
        ),
    )
    capability = LabCapability(
        id="cap_pdf",
        canonical_test_id="cross_cut_adhesion",
        method_ids=["iso_2409"],
        declared_constraints="23 °C only; bake coverage pending",
        notes="Preparation is excluded from the reference rate",
        price=Decimal("35.00"),
        currency="USD",
        pricing_basis="per panel",
    )
    statuses = ["in_house", "method_gap", "test_gap", "condition_gap", "needs_review", "excluded"]
    lines = [
        GapLine(
            requirement=requirement,
            canonical_test_id="cross_cut_adhesion",
            canonical_test_name="Cross-cut adhesion",
            match_basis="MATCH_BASIS_SENTINEL: Exact curated alias and method identity",
            method_ids=["iso_2409"],
            considered_method_codes=["ISO 2409"],
            capability=capability,
            lab_method_identifiers=["ISO 2409"],
            status=statuses[index % len(statuses)],
            reasons=[f"RAW_REASON_SENTINEL_{index}: conditions still need an engineer"],
            extraction_review_state="needs_attention" if index == 4 else "ready",
            extraction_review_reasons=["RAW_REVIEW_REASON_SENTINEL"] if index == 4 else [],
        )
        for index, requirement in enumerate(run.extraction.requirements)
    ]
    return ReportSnapshot(
        report_id="report_pdf_1234",
        created_at=datetime(2026, 10, 17, 14, 3, 2, tzinfo=UTC),
        document_id=document_id,
        original_name="customer-original.pdf",
        document_sha256="b" * 64,
        source_run=run,
        source_review=RequirementReviewSet(document_id=document_id, run_id=run.run_id, version=3),
        lab=default_demo_lab().model_copy(update={"version": 7}),
        catalog=load_reference_catalog(),
        workspace=ReportWorkspace(document_id=document_id, run_id=run.run_id, version=2),
        input_fingerprint="c" * 64,
        lines=lines,
        warnings=["TECHNICAL_WARNING_SENTINEL: source fragments need diagnostic inspection."],
    )


def _pdf_text(data: bytes) -> str:
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        return "\n".join(
            page.get_text(clip=pymupdf.Rect(0, 60, page.rect.width, page.rect.height - 40))
            for page in pdf
        )


def _compact(text: str) -> str:
    # Normalize typography ligatures and layout whitespace, not source content.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text))


def test_pdf_is_readable_paginated_draft_with_business_identity_and_no_mutation() -> None:
    snapshot = _snapshot()
    before = snapshot.model_dump_json()

    data = render_report_pdf(snapshot)

    assert data.startswith(b"%PDF-")
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        for index, page in enumerate(pdf, start=1):
            assert page.rect == pymupdf.paper_rect("a4")
            text = _compact(page.get_text())
            assert "SCIONE / LAB CAPABILITY GAP REPORT" in text
            assert f"Page {index} of {len(pdf)}" in text
            assert "DRAFT" in text
    text = _compact(_pdf_text(data))
    for expected in [
        "Lab capability gap report",
        "Customer coating specification",
        "SPEC-PDF-01",
        "Rev C",
        "Demo Coatings Lab",
        "17 Oct 2026",
        "Review summary",
    ]:
        assert expected in text
    assert snapshot.model_dump_json() == before
    assert ReportSnapshot.model_validate_json(before) == snapshot


def test_pdf_omits_developer_diagnostics_but_preserves_them_in_json() -> None:
    snapshot = _snapshot()
    snapshot.lines[4].candidate_test_ids = ["pull_off_adhesion", "cross_cut_adhesion"]
    text = _compact(_pdf_text(render_report_pdf(snapshot)))

    for technical in [
        "req_pdf_",
        "cross_cut_adhesion",
        "pull_off_adhesion",
        "cap_pdf",
        "iso_2409",
        "MATCH_BASIS_SENTINEL",
        "RAW_REASON_SENTINEL",
        "RAW_REVIEW_REASON_SENTINEL",
        "TECHNICAL_WARNING_SENTINEL",
        "RAW_RESPONSE_SENTINEL",
        "MODEL_PROVIDER_SENTINEL",
        "MODEL_NAME_SENTINEL",
        "customer_fixture",
        "pdf_run_one",
        snapshot.document_id,
        snapshot.document_sha256,
        snapshot.input_fingerprint,
        "SOURCE ROW",
        "Candidate test IDs",
        "Content review version",
        "workspace version",
        "Technical/source review appendix",
        "Report provenance",
    ]:
        assert technical not in text
    archive = snapshot.model_dump_json()
    for retained in [
        "MATCH_BASIS_SENTINEL",
        "RAW_REASON_SENTINEL",
        "RAW_REVIEW_REASON_SENTINEL",
        "TECHNICAL_WARNING_SENTINEL",
        "RAW_RESPONSE_SENTINEL",
        "SOURCE ROW",
    ]:
        assert retained in archive


def test_active_requirements_keep_business_fields_and_excluded_rows_stay_minimal() -> None:
    snapshot = _snapshot()
    snapshot.source_review.decisions["req_pdf_4"] = RequirementReview(
        requirement_id="req_pdf_4",
        status="needs_review",
        note="Please confirm whether aluminum panels are required",
        updated_at=datetime(2026, 10, 17, tzinfo=UTC),
    )
    snapshot.lines[4].mapping_choice = MappingChoice(
        canonical_test_id="cross_cut_adhesion",
        capability_verdict="unreviewed",
        note="Lab engineer must confirm the customer method",
    )
    excluded = snapshot.lines[5].requirement
    excluded.conditions[0].raw_value = "EXCLUDED_CONDITION_SENTINEL"
    excluded.acceptance_criteria[0].raw_value = "EXCLUDED_CRITERION_SENTINEL"
    excluded.applicability = ["EXCLUDED_APPLICABILITY_SENTINEL"]
    snapshot.source_review.decisions[excluded.id] = RequirementReview(
        requirement_id=excluded.id,
        status="excluded",
        note="Customer removed this test from the requested scope",
        updated_at=datetime(2026, 10, 17, tzinfo=UTC),
    )

    text = _compact(_pdf_text(render_report_pdf(snapshot)))

    for index in range(6):
        assert f"Coating check [{index:03d}]" in text
    for expected in [
        "ISO 2409",
        "2020",
        "Required method",
        "Lab methods",
        "23 °C only; bake coverage pending",
        "Preparation is excluded from the reference rate",
        "Spacing",
        "2 mm",
        "All coated panels",
        "Rating",
        "GT 0 to 1",
        "After conditioning",
        "Both steel and aluminum",
        "Do not substitute methods",
        "Please confirm whether aluminum panels are required",
        "Lab engineer must confirm the customer method",
        "Customer removed this test from the requested scope",
        "Source pages",
        "Different method",
        "Not listed in lab catalog",
        "Match needs clarification",
        "Outside declared scope",
        "Excluded from scope",
    ]:
        assert expected in text
    assert "EXCLUDED_CONDITION_SENTINEL" not in text
    assert "EXCLUDED_CRITERION_SENTINEL" not in text
    assert "EXCLUDED_APPLICABILITY_SENTINEL" not in text
    assert "SOURCE ROW" not in text


def test_source_page_pointers_replace_full_quoted_evidence() -> None:
    snapshot = _snapshot(count=1)
    snapshot.lines[0].requirement.evidence.append(
        snapshot.lines[0]
        .requirement.evidence[0]
        .model_copy(update={"page": 9, "quote": "SECOND_QUOTE_SENTINEL"})
    )

    text = _compact(_pdf_text(render_report_pdf(snapshot)))

    assert re.search(r"Source pages\s*:\s*1\s*,\s*9", text)
    assert "SOURCE ROW" not in text
    assert "SECOND_QUOTE_SENTINEL" not in text


def test_readable_lab_test_name_is_shown_without_internal_identity_fields() -> None:
    snapshot = _snapshot(count=1)

    text = _compact(_pdf_text(render_report_pdf(snapshot)))

    assert "Lab test: Cross-cut adhesion" in text
    assert "cross_cut_adhesion" not in text
    assert "cap_pdf" not in text


def test_listed_method_does_not_imply_lab_scope_has_been_confirmed() -> None:
    snapshot = _snapshot(count=1)

    unreviewed = _compact(_pdf_text(render_report_pdf(snapshot)))

    assert "Method listed — scope to confirm" in unreviewed
    assert "Scope confirmed by lab reviewer" not in unreviewed
    snapshot.lines[0].mapping_choice = MappingChoice(
        canonical_test_id="cross_cut_adhesion",
        capability_verdict="supported",
        lab_fingerprint=lab_fingerprint(snapshot.lab),
        note="Lab engineer checked the conditions against current equipment",
    )

    confirmed = _compact(_pdf_text(render_report_pdf(snapshot)))

    assert "Scope confirmed by lab reviewer" in confirmed
    assert "Lab engineer checked the conditions against current equipment" in confirmed


@pytest.mark.parametrize("fingerprint", [None, "d" * 64])
def test_unbound_or_stale_scope_approval_stays_pending_in_pdf(fingerprint: str | None) -> None:
    snapshot = _snapshot(count=1)
    snapshot.lines[0].mapping_choice = MappingChoice(
        canonical_test_id="cross_cut_adhesion",
        capability_verdict="supported",
        lab_fingerprint=fingerprint,
        note="An earlier saved scope decision",
    )

    text = _compact(_pdf_text(render_report_pdf(snapshot)))

    assert "Method listed — scope to confirm" in text
    assert "Scope confirmed by lab reviewer" not in text


@pytest.mark.parametrize("status", ["method_gap", "test_gap", "condition_gap", "needs_review"])
def test_current_saved_approval_does_not_override_a_pending_or_gap_result(status: str) -> None:
    snapshot = _snapshot(count=1)
    snapshot.lines[0].status = status
    snapshot.lines[0].mapping_choice = MappingChoice(
        canonical_test_id="cross_cut_adhesion",
        capability_verdict="supported",
        lab_fingerprint=lab_fingerprint(snapshot.lab),
        note="Saved engineer decision before this matching result",
    )

    text = _compact(_pdf_text(render_report_pdf(snapshot)))

    assert "Scope confirmed by lab reviewer" not in text


def test_needs_content_review_is_visible_without_raw_technical_flags() -> None:
    snapshot = _snapshot(count=1)
    snapshot.lines[0].extraction_review_state = "needs_attention"
    snapshot.lines[0].extraction_review_reasons = ["RAW_REVIEW_REASON_SENTINEL"]

    text = _compact(_pdf_text(render_report_pdf(snapshot)))

    assert "Customer details need confirmation against the specification" in text
    assert "RAW_REVIEW_REASON_SENTINEL" not in text


def test_thirty_five_typical_requirements_are_compact_without_duplicate_index() -> None:
    snapshot = _snapshot(count=35)

    data = render_report_pdf(snapshot)
    text = _compact(_pdf_text(data))

    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        assert len(pdf) < 10
    for line in snapshot.lines:
        assert text.count(line.requirement.original_test_name) == 1
    assert "Detailed requirements and source evidence" not in text
    assert "Requirement comparison" not in text
    assert "Technical/source review appendix" not in text


def test_unicode_chinese_greek_inequality_accents_and_raw_values_survive() -> None:
    snapshot = _snapshot(count=1)
    row = snapshot.source_run.extraction.requirements[0]
    row.original_test_name = "划格附着力 · ΔE · résistance"
    row.conditions[0].raw_value = "−20 °C; ≥ 50 μm; ≤ 1.5; 日本語"
    row.acceptance_criteria[0].raw_value = "无裂纹或脱落"
    row.evidence[0].quote = "SOURCE_QUOTE_ONLY_测试要求：涂层 ≥ 50 μm，ΔE ≤ 1.5。"
    snapshot.lines[0].requirement = row

    text = _compact(_pdf_text(render_report_pdf(snapshot)))

    for expected in [
        "划格附着力",
        "ΔE",
        "résistance",
        "−20 °C; ≥ 50 μm; ≤ 1.5; 日本語",
        "无裂纹或脱落",
    ]:
        assert expected in text
    assert "SOURCE_QUOTE_ONLY" not in text
    assert "\ufffd" not in text


def test_very_long_operational_values_flow_across_pages_without_cutting_the_tail() -> None:
    snapshot = _snapshot(count=1)
    row = snapshot.source_run.extraction.requirements[0]
    row.conditions[0].raw_value = (
        "Retain each condition and qualification. " * 170
    ) + "CONDITION_END"
    row.evidence[0].quote = ("Unneeded source archive. " * 170) + "EVIDENCE_END"
    snapshot.lines[0].requirement = row

    data = render_report_pdf(snapshot)

    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        assert len(pdf) > 1
        text = _compact(_pdf_text(data))
        assert text.count("Retain each condition and qualification.") == 170
        assert "CONDITION_END" in text
        assert "EVIDENCE_END" not in text
        for page in pdf:
            for word in page.get_text("words"):
                if 60 < word[1] < page.rect.height - 40:
                    assert word[0] >= 41
                    assert word[2] <= page.rect.width - 40
                    # Body content ends 49 pt above the page edge, leaving a
                    # separate footer at 31 pt. Allow subpixel font rounding.
                    assert word[3] <= page.rect.height - 48.8


def test_source_html_is_literal_text_not_links_scripts_assets_or_layout() -> None:
    snapshot = _snapshot(count=1)
    row = snapshot.source_run.extraction.requirements[0]
    attack = '<script>HIDE_THIS</script><img src="https://example.invalid/a.png">'
    row.original_test_name = "Literal <b>untrusted heading</b>"
    row.conditions[0].raw_value = attack
    snapshot.lines[0].requirement = row

    data = render_report_pdf(snapshot)
    text = _compact(_pdf_text(data))

    assert "Literal <b>untrusted heading</b>" in text
    assert attack in text
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        assert not any(page.get_links() for page in pdf)
        assert not any(page.get_images() for page in pdf)


def test_unbroken_long_tokens_are_wrapped_instead_of_disappearing_off_page() -> None:
    snapshot = _snapshot(count=1)
    row = snapshot.source_run.extraction.requirements[0]
    row.conditions[0].raw_value = "X" * 7000 + "UNBROKEN_END"
    snapshot.lines[0].requirement = row
    before = snapshot.model_dump_json()

    data = render_report_pdf(snapshot)
    text = _pdf_text(data)

    assert text.count("X") == 7000
    assert "UNBROKEN_END" in text
    assert snapshot.model_dump_json() == before
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        for page in pdf:
            assert all(word[2] <= page.rect.width - 39.8 for word in page.get_text("words"))


def test_reference_prices_without_quantity_do_not_create_a_quote_total() -> None:
    snapshot = _snapshot(count=1)
    text = _compact(_pdf_text(render_report_pdf(snapshot)))
    assert "USD 35.00" in text
    assert "per panel" in text
    assert "Reference rate" in text
    assert "No confirmed line estimates yet" in text
    assert "not a quote; quantity and scope not priced" in text
    assert "Draft line estimate:" not in text
    assert "1 ×" not in text


def test_rate_for_a_different_method_is_not_offered_as_the_requested_test_price() -> None:
    snapshot = _snapshot(count=1)
    snapshot.lines[0].status = "method_gap"
    snapshot.lines[0].capability = snapshot.lines[0].capability.model_copy(
        update={"price": Decimal("999.99")}
    )

    text = _compact(_pdf_text(render_report_pdf(snapshot)))

    assert "Different method" in text
    assert "USD 999.99" not in text
    assert "Draft line estimate" not in text


def test_zero_price_and_multiple_currencies_remain_separate_with_explicit_quantities() -> None:
    snapshot = _snapshot(count=3)
    for index, currency in enumerate(("USD", "EUR")):
        amount = Decimal("0.00") if index == 0 else Decimal("120.00")
        snapshot.lines[index].quote = QuoteLine(
            quantity=Decimal("2"),
            unit_price=amount,
            currency=currency,
            pricing_basis="per panel",
            subtotal=amount * 2,
            note=f"Engineer explicitly scoped pair {index}",
        )
    snapshot.totals_by_currency = {"USD": Decimal("0.00"), "EUR": Decimal("240.00")}

    text = _compact(_pdf_text(render_report_pdf(snapshot)))

    assert "USD 0.00" in text
    assert "EUR 240.00" in text
    assert "2 × USD 0.00 / per panel = USD 0.00" in text
    assert "2 × EUR 120.00 / per panel = EUR 240.00" in text
    assert "Engineer explicitly scoped pair 0" in text
    assert "Engineer explicitly scoped pair 1" in text
    assert "Draft pricing" in text
    assert "Subtotals cover priced lines only, not the whole job" in text


def test_empty_report_explains_no_rows_without_fabricating_records() -> None:
    text = _compact(_pdf_text(render_report_pdf(_snapshot(count=0))))
    assert "No active test requirements are included in this report" in text
    assert "Coating check" not in text


def test_inconsistent_mutated_snapshot_is_rejected_not_silently_repaired() -> None:
    snapshot = _snapshot()
    snapshot.lines.pop()
    with pytest.raises(ReportPDFError, match="Every extracted requirement"):
        render_report_pdf(snapshot)


def test_renderer_failure_is_a_report_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_story(*args: object, **kwargs: object) -> None:
        raise RuntimeError("offline renderer failed")

    monkeypatch.setattr(pymupdf, "Story", broken_story)
    with pytest.raises(ReportPDFError, match="offline renderer failed"):
        render_report_pdf(_snapshot())
