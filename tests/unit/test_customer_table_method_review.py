"""Exact printed customer procedures resolve only a missing-method prompt."""

from __future__ import annotations

import hashlib

import pymupdf
import pytest
from test_spec_preview import ruled_table_pdf_bytes
from test_spec_review import _document, _run

from scione.catalogs.seed import load_reference_catalog
from scione.schemas import DocumentPage
from scione.specifications.models import (
    CustomerRequirement,
    RequirementEvidence,
    RequirementValue,
)
from scione.specifications.pdf_preview import pdf_quote_tokens
from scione.specifications.review import _proved_customer_table_method, assess_requirements

MISSING_METHOD = "No test method was identified; check the source."


def procedure_source(*, procedure="Wave-scan structure\nmeter", duplicate=False):
    """Reuse the ruled-grid fixture, changing only its printed cell contents."""
    with pymupdf.open(
        stream=ruled_table_pdf_bytes(duplicate_row=duplicate, column_order=True),
        filetype="pdf",
    ) as pdf:
        page = pdf[0]
        page.add_redact_annot(page.rect)
        page.apply_redactions(images=0, graphics=0)
        rows = [
            ("Test item", "Customer procedure", "Initial", "Aged"),
            ("Long wave (LW)", procedure, "<= 3", "<= 6"),
            ("Short wave (SW)", "Other instrument", "<= 4", "<= 7"),
        ]
        if duplicate:
            rows.append(rows[1])
        xs = (30, 180, 310, 450)
        # Column drawing order differs from the full row's geometric order.
        for column in range(4):
            for index, cells in enumerate(rows):
                page.insert_text(
                    (xs[column] + 5, 60 + 40 * index), cells[column], fontsize=9,
                )
        quote = page.get_text("text", clip=pymupdf.Rect(30, 80, 550, 120), sort=True)
        pages = []
        for number, source_page in enumerate(pdf, start=1):
            text = source_page.get_text("text", sort=True)
            pages.append(DocumentPage(
                page_number=number, text=text, character_count=len(text),
                non_whitespace_character_count=len("".join(text.split())),
            ))
        data = pdf.tobytes()
    document = _document().model_copy(update={
        "sha256": hashlib.sha256(data).hexdigest(), "page_count": len(pages), "pages": pages,
    })
    return data, document, quote


def procedure_requirement(quote):
    return CustomerRequirement(
        id="req_001", original_test_name="Long wave (LW)",
        conditions=[RequirementValue(name="Instrument", raw_value="Wave-scan structure meter")],
        acceptance_criteria=[RequirementValue(name="Initial long wave", raw_value="<= 3")],
        evidence=[RequirementEvidence(page=1, quote=quote)],
    )


def test_wrapped_no_pipe_customer_method_is_proved_without_mutating_raw_run():
    data, document, quote = procedure_source()
    assert "|" not in quote
    # The wrapped final instrument word comes after the numerical limit cells.
    assert pdf_quote_tokens(quote)[-1] == "meter"
    row = procedure_requirement(quote)
    run = _run(rows=[row])
    before = run.model_dump_json()

    assert _proved_customer_table_method(row, load_reference_catalog(), data)
    assert MISSING_METHOD in assess_requirements(run, document)[row.id].reasons
    assessment = assess_requirements(run, document, pdf_bytes=data)[row.id]
    assert MISSING_METHOD not in assessment.reasons
    assert assessment.state == "ready"
    assert row.requested_methods == []
    assert run.model_dump_json() == before


@pytest.mark.parametrize("cause", [
    "different_method", "changed_number", "wrong_owner", "duplicate_row",
    "no_literal_procedure", "no_conditions", "partial_quote", "wrong_page",
])
def test_incomplete_or_wrong_customer_method_proof_keeps_missing_method(cause):
    procedure = {
        "different_method": "Different optical meter",
        "no_literal_procedure": "",
    }.get(cause, "Wave-scan structure\nmeter")
    data, document, quote = procedure_source(
        procedure=procedure, duplicate=cause == "duplicate_row",
    )
    row = procedure_requirement(quote)
    if cause == "changed_number":
        row.evidence[0].quote = quote.replace("<= 3", "<= 8")
    elif cause == "wrong_owner":
        row.original_test_name = "Short wave (SW)"
    elif cause == "no_conditions":
        row.conditions = []
    elif cause == "partial_quote":
        row.evidence[0].quote = "Long wave (LW) Wave-scan structure meter"
    elif cause == "wrong_page":
        row.evidence[0].page = 2

    assert not _proved_customer_table_method(row, load_reference_catalog(), data)
    assessment = assess_requirements(_run(rows=[row]), document, pdf_bytes=data)[row.id]
    assert assessment.state == "needs_attention"
    assert MISSING_METHOD in assessment.reasons


def test_exact_method_proof_cannot_swallow_scientific_or_second_citation_issues():
    data, document, quote = procedure_source()
    row = procedure_requirement(quote)
    row.evidence.append(RequirementEvidence(page=1, quote="Invented unprinted requirement"))
    scientific_issue = "req_001: the requested long-wave acceptance limit is ambiguous."
    run = _run(rows=[row], issues=[scientific_issue])
    before = run.model_dump_json()

    assessment = assess_requirements(run, document, pdf_bytes=data)[row.id]

    assert assessment.state == "needs_attention"
    assert MISSING_METHOD not in assessment.reasons
    assert scientific_issue in assessment.reasons
    assert any("evidence 2" in reason for reason in assessment.reasons)
    assert row.requested_methods == []
    assert run.model_dump_json() == before


def test_nonunique_curated_owner_is_not_customer_method_proof():
    data, _, quote = procedure_source()
    row = procedure_requirement(quote)
    catalog = load_reference_catalog().model_copy(deep=True)
    original = catalog.test_by_id("long_wave")
    catalog.tests.append(original.model_copy(update={"id": "different_long_wave"}))
    assert not _proved_customer_table_method(row, catalog, data)
