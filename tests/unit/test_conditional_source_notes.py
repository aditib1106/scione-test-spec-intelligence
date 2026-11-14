"""An inactive method's future criterion is a note, never invented lab support."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from scione.providers import TokenUsage
from scione.schemas import DocumentPage, IngestedDocument
from scione.specifications.conditional_notes import (
    conditional_route_proofs,
    conditional_source_notices,
)
from scione.specifications.models import (
    CustomerDocumentMetadata,
    CustomerExtraction,
    CustomerExtractionCall,
    CustomerExtractionRun,
    CustomerRequirement,
    RequestedMethod,
    RequirementEvidence,
    RequirementValue,
)
from scione.specifications.review import assess_requirements

PRIMARY = "ISO 2468:2022"
OPTIONAL = "ASTM D9753-19"
PRIMARY_REQUEST = f"For this assessment the receiving site should use {PRIMARY}."
PRIMARY_CLASS = "ISO class 0 or 1 is required."
OPTIONAL_REQUEST = f"The receiving lab may request {OPTIONAL}; customer approval is required."
FUTURE = (
    "If an ASTM route is approved, our team will issue the corresponding acceptance class "
    "before testing."
)
ISSUE = (
    f"req_001: The {OPTIONAL} route has no stated acceptance class; the customer will "
    "issue it only after approval, so no ASTM-based acceptance limit can be recorded."
)


def fixture(issue: str | None = ISSUE):
    quotes = [PRIMARY_REQUEST + " " + PRIMARY_CLASS, OPTIONAL_REQUEST, FUTURE]
    text = "\n".join(quotes)
    document = IngestedDocument(
        document_id="conditional_source", file_name="fictional.pdf", sha256="a" * 64,
        page_count=1, extraction_status="ready", text_reading_order="geometric",
        pages=[DocumentPage(
            page_number=1, text=text, character_count=len(text),
            non_whitespace_character_count=len("".join(text.split())),
        )],
    )
    row = CustomerRequirement(
        id="req_001", original_test_name="Surface retention",
        requested_methods=[
            RequestedMethod(code=PRIMARY, role="primary"),
            RequestedMethod(code=OPTIONAL, role="optional customer approval required"),
        ],
        acceptance_criteria=[RequirementValue(name="Primary class", raw_value=PRIMARY_CLASS)],
        applicability=[FUTURE],
        evidence=[RequirementEvidence(page=1, quote=quote) for quote in quotes],
    )
    roles = [
        {"code": PRIMARY, "role": "primary", "page": 1, "quote": PRIMARY_REQUEST},
        {"code": OPTIONAL, "role": "optional_approval_required", "page": 1,
         "quote": OPTIONAL_REQUEST},
    ]
    wire = {
        "document": {"title": None, "identifier": None, "revision": None},
        "document_notices": [], "shared_requirements": [],
        "review_issues": [issue] if issue else [],
        "requirements": [{
            "test": row.original_test_name, "methods": [PRIMARY, OPTIONAL],
            "method_roles": roles, "conditions": [], "criteria": [PRIMARY_CLASS],
            "scope": [FUTURE], "instructions": [], "atomic_claims": [], "uncertainty": None,
            "evidence": [{"page": 1, "quote": quote} for quote in quotes],
        }],
    }
    raw = json.dumps(wire)
    run = CustomerExtractionRun(
        run_id="conditional_run", started_at=datetime.now(UTC), document_id=document.document_id,
        strategy="compact_document", prompt_version="offline", schema_version="0.1",
        provider="static", model="fixture", latency_ms=0, usage=TokenUsage(), raw_response=raw,
        calls=[CustomerExtractionCall(
            call_index=1, focus_pages=[1], provider="static", model="fixture",
            latency_ms=0, usage=TokenUsage(), raw_response=raw,
        )],
        extraction=CustomerExtraction(
            schema_version="0.1", document=CustomerDocumentMetadata(), requirements=[row],
            review_issues=[issue] if issue else [],
        ),
    )
    return run, document


def change_wire(run, mutate):
    wire = json.loads(run.calls[0].raw_response)
    mutate(wire["requirements"][0])
    raw = json.dumps(wire)
    run.calls[0].raw_response = raw
    run.raw_response = raw


@pytest.mark.parametrize("issue", [
    ISSUE,
    f"Acceptance class for {OPTIONAL} is not stated; the customer will provide it after approval.",
])
def test_proved_inactive_future_class_is_a_visible_note_with_immutable_original(issue):
    run, document = fixture(issue)
    original = run.model_dump_json()
    assessed = assess_requirements(run, document)["req_001"]
    assert assessed.state == "ready"
    assert assessed.reasons == ()
    assert assessed.notices == (issue,)
    assert asdict(assessed)["notices"] == (issue,)
    assert run.model_dump_json() == original


@pytest.mark.parametrize("mutation", [
    "primary_warning", "extra_quantity", "other_physics", "extra_ordinal", "negated_future",
    "unknown_row", "missing_primary_class",
    "missing_primary_method", "wrong_primary_class", "unverified_optional_role",
    "unverified_primary_role",
    "invented_role_quote", "wrong_role_page", "unowned_future", "invented_future_quote",
    "unknown_expanded_role", "ambiguous_family", "negated_primary_instruction",
    "withdrawn_future_clause", "example_optional_request", "withdrawn_primary_class",
])
def test_unknown_or_mixed_concerns_remain_blocking(mutation):
    run, document = fixture()
    row = run.extraction.requirements[0]
    if mutation == "primary_warning":
        run.extraction.review_issues = [ISSUE.replace(OPTIONAL, PRIMARY)]
    elif mutation == "extra_quantity":
        run.extraction.review_issues = [ISSUE + " Temperature 60 °C is missing."]
    elif mutation == "other_physics":
        run.extraction.review_issues = [ISSUE + " The specimen material is ambiguous."]
    elif mutation == "extra_ordinal":
        run.extraction.review_issues = [ISSUE + " Acceptance class A was specified."]
    elif mutation == "negated_future":
        run.extraction.review_issues = [ISSUE + " The customer will not issue it after approval."]
    elif mutation == "unknown_row":
        run.extraction.review_issues = [ISSUE.replace("req_001", "req_999")]
    elif mutation == "missing_primary_class":
        row.acceptance_criteria = [RequirementValue(name="Future class", raw_value=FUTURE)]
    elif mutation == "missing_primary_method":
        row.requested_methods = [row.requested_methods[1]]
    elif mutation == "wrong_primary_class":
        row.acceptance_criteria[0].raw_value = "ISO class 9 is required."
    elif mutation == "unverified_optional_role":
        change_wire(run, lambda wire: wire.update(method_roles=wire["method_roles"][:1]))
    elif mutation == "unverified_primary_role":
        change_wire(run, lambda wire: wire.update(method_roles=wire["method_roles"][1:]))
    elif mutation == "invented_role_quote":
        change_wire(run, lambda wire: wire["method_roles"][1].update(
            quote=f"The supplier may request {OPTIONAL}, but this quote is invented."
        ))
    elif mutation == "wrong_role_page":
        change_wire(run, lambda wire: wire["method_roles"][1].update(page=2))
    elif mutation == "unowned_future":
        row.evidence = [evidence for evidence in row.evidence if evidence.quote != FUTURE]
    elif mutation == "invented_future_quote":
        row.evidence = [evidence.model_copy(update={"quote": FUTURE + " Invented."})
                        if evidence.quote == FUTURE else evidence for evidence in row.evidence]
    elif mutation == "unknown_expanded_role":
        row.requested_methods[1].role = "alternative"
    elif mutation == "ambiguous_family":
        row.requested_methods.append(RequestedMethod(
            code="ASTM D5319-18", role="optional customer approval required"
        ))
    elif mutation == "negated_primary_instruction":
        source = PRIMARY_REQUEST.replace("should use", "must not use")
        document.pages[0].text = document.pages[0].text.replace(PRIMARY_REQUEST, source)
        row.evidence[0].quote = row.evidence[0].quote.replace(PRIMARY_REQUEST, source)
        change_wire(run, lambda wire: wire["method_roles"][0].update(quote=source))
    elif mutation == "withdrawn_future_clause":
        source = "Withdrawn template: " + FUTURE
        document.pages[0].text = document.pages[0].text.replace(FUTURE, source)
        row.evidence[-1].quote = source
    elif mutation == "example_optional_request":
        source = "Hypothetical example: " + OPTIONAL_REQUEST
        document.pages[0].text = document.pages[0].text.replace(OPTIONAL_REQUEST, source)
        row.evidence[1].quote = source
        change_wire(run, lambda wire: wire["method_roles"][1].update(quote=source))
    elif mutation == "withdrawn_primary_class":
        source = "Obsolete limit: " + PRIMARY_CLASS
        document.pages[0].text = document.pages[0].text.replace(PRIMARY_CLASS, source)
        row.evidence[0].quote = row.evidence[0].quote.replace(PRIMARY_CLASS, source)
        row.acceptance_criteria[0].raw_value = source
    assessed = assess_requirements(run, document)["req_001"]
    assert assessed.state == "needs_attention"
    assert run.extraction.review_issues[0] in assessed.reasons
    assert assessed.notices == ()


def test_only_the_proved_owner_gets_a_notice_and_other_owners_keep_the_exact_warning():
    issue = ISSUE.removeprefix("req_001: ")
    run, document = fixture(issue)
    other = run.extraction.requirements[0].model_copy(deep=True)
    other.id = "req_002"
    other.original_test_name = "A separate request"
    other.requested_methods = [RequestedMethod(code=OPTIONAL, role="primary")]
    run.extraction.requirements.append(other)
    assessments = assess_requirements(run, document)
    assert assessments["req_001"].notices == (issue,)
    assert assessments["req_001"].state == "ready"
    assert assessments["req_002"].notices == ()
    assert issue in assessments["req_002"].reasons
    assert assessments["req_002"].state == "needs_attention"


def test_physical_route_proof_does_not_depend_on_model_emitting_a_warning():
    run, document = fixture(None)
    proof = conditional_route_proofs(run, document)["req_001"]
    assert proof["primary_method_code"] == PRIMARY
    assert proof["optional_method_codes"] == [OPTIONAL]
    assert proof["source_run_id"] == run.run_id
    assert proof["source_document_sha256"] == document.sha256
    assert {item["page"] for item in proof["source_evidence"]} == {1}
    assert conditional_source_notices(run, document) == {}
    assert run.extraction.review_issues == []
