"""Input-window merging must not invent equivalence or lose scoped source facts."""

from decimal import Decimal

import pytest

from scione.specifications.chunk_aggregation import merge_chunk_extractions
from scione.specifications.models import (
    CustomerDocumentMetadata,
    CustomerDocumentNotice,
    CustomerExtraction,
    CustomerRequirement,
    RequestedMethod,
    RequirementEvidence,
    RequirementValue,
)
from scione.structured.models import AtomicClaim


def requirement(identifier="req_001", **updates):
    row = CustomerRequirement(
        id=identifier,
        original_test_name="Humidity exposure",
        requested_methods=[RequestedMethod(code="ISO 6270-2:2017")],
        conditions=[RequirementValue(name="Temperature", raw_value="40.01 °C")],
        applicability=["Painted steel panels"],
        evidence=[
            RequirementEvidence(page=6, quote="Expose painted steel panels."),
            RequirementEvidence(page=10, quote="Temperature: 40.01 °C."),
        ],
        atomic_claims=[AtomicClaim(
            id=f"{identifier}_local_atom",
            parameter_id="temperature",
            label="Temperature",
            operator="=",
            value=Decimal("40.01"),
            unit="°C",
            stage="humidity_exposure",
            sequence=1,
            raw_text="Temperature: 40.01 °C.",
            source_pages=[10],
            source_context="Painted steel panels",
        )],
    )
    return row.model_copy(update=updates, deep=True)


def extraction(*rows, issues=None, **updates):
    return CustomerExtraction(
        schema_version="0.1",
        document=CustomerDocumentMetadata(title="Customer coating specification"),
        requirements=list(rows),
        review_issues=issues or [],
        **updates,
    )


def test_empty_input_is_not_a_valid_aggregation():
    with pytest.raises(ValueError, match="at least one"):
        merge_chunk_extractions([])


def test_overlap_dedup_ignores_local_ids_and_citation_order_without_changing_inputs():
    first = requirement("req_009")
    duplicate = requirement("req_003")
    duplicate.evidence.reverse()
    a = extraction(first, issues=["req_009: verify exposure duration."])
    b = extraction(duplicate, issues=["req_003: verify exposure duration."])
    before = [part.model_dump_json() for part in [a, b]]

    merged = merge_chunk_extractions([a, b])

    assert len(merged.requirements) == 1
    row = merged.requirements[0]
    assert row.id == "req_001"
    assert row.atomic_claims[0].id == "req_001_claim_1"
    assert row.atomic_claims[0].value == Decimal("40.01")
    assert row.evidence == first.evidence
    assert [item.page for item in row.evidence] == [6, 10]
    assert merged.review_issues == ["req_001: verify exposure duration."]
    assert [part.model_dump_json() for part in [a, b]] == before


@pytest.mark.parametrize("field,value", [
    ("stage", "after_humidity"),
    ("sequence", 2),
    ("source_context", "Painted aluminum panels"),
    ("configuration", "horizontal"),
    ("unit", "K"),
    ("value", Decimal("40.02")),
])
def test_matching_names_and_methods_never_collapse_distinct_claim_scope(field, value):
    first = requirement()
    variant = requirement()
    variant.atomic_claims[0] = variant.atomic_claims[0].model_copy(update={field: value})
    merged = merge_chunk_extractions([extraction(first), extraction(variant)])
    assert [row.id for row in merged.requirements] == ["req_001", "req_002"]
    assert [row.atomic_claims[0].id for row in merged.requirements] == [
        "req_001_claim_1", "req_002_claim_1",
    ]


def test_distinct_material_method_and_raw_conditions_remain_separate():
    first = requirement()
    material = requirement(applicability=["Painted plastic panels"])
    method = requirement(requested_methods=[RequestedMethod(code="ISO 6270-2:2025")])
    condition = requirement(conditions=[RequirementValue(name="Temperature", raw_value="41 °C")])
    merged = merge_chunk_extractions([
        extraction(first), extraction(material), extraction(method), extraction(condition),
    ])
    assert len(merged.requirements) == 4


def test_issue_id_mapping_is_per_chunk_and_simultaneous_and_unknown_ids_survive():
    first = extraction(requirement("req_001"))
    second = extraction(
        requirement("req_001", applicability=["Plastic panels"]),
        requirement("req_002", applicability=["Aluminum panels"]),
        issues=["req_001 and req_002: inspect scope; req_unknown remains unresolved."],
    )
    merged = merge_chunk_extractions([first, second])
    assert merged.review_issues == [
        "req_002 and req_003: inspect scope; req_unknown remains unresolved."
    ]


def test_metadata_conflict_keeps_first_and_notices_are_exact_deduplicated_copies():
    notice = CustomerDocumentNotice(page=1, quote="Method identifiers are fictional.")
    first = extraction(requirement(), document_notices=[notice])
    second = extraction(requirement(), document_notices=[notice.model_copy(deep=True)])
    second.document.revision = "Rev B"

    merged = merge_chunk_extractions([first, second])

    assert merged.document == first.document
    assert merged.document is not first.document
    assert merged.document_notices == [notice]
    assert merged.document_notices[0] is not notice
    assert any("differing document metadata" in issue for issue in merged.review_issues)


def test_empty_window_is_allowed_and_final_model_roundtrips():
    merged = merge_chunk_extractions([extraction(), extraction(requirement())])
    assert len(merged.requirements) == 1
    assert CustomerExtraction.model_validate_json(merged.model_dump_json()) == merged


def test_exact_unique_name_prefix_scopes_issue_before_global_id_mapping():
    first = extraction(requirement())
    issue = "pressure WATER jet adhesion: source does not define the 10 cm reference."
    second = extraction(
        requirement("req_009", original_test_name="Pressure water jet adhesion"),
        issues=[issue],
    )
    merged = merge_chunk_extractions([first, second])
    assert merged.review_issues == [f"req_002: {issue}"]


@pytest.mark.parametrize("issue", [
    "Humidity exposure requires attention.",
    "Humidity: inspect the exposure conditions.",
    "ISO 6270-2: inspect the exposure conditions.",
    "The Humidity exposure: duration needs checking.",
    "Humidity exposure: check alongside req_unknown.",
])
def test_issue_scoping_does_not_infer_names_methods_or_change_existing_id_issues(issue):
    merged = merge_chunk_extractions([extraction(requirement(), issues=[issue])])
    assert merged.review_issues == [issue]


def test_same_named_scoped_variants_across_chunks_leave_generic_issue_unscoped():
    issue = "Humidity exposure: verify the duration."
    merged = merge_chunk_extractions([
        extraction(requirement(), issues=[issue]),
        extraction(requirement(applicability=["Plastic panels"])),
    ])
    assert merged.review_issues == [issue]


def test_repeated_exact_overlap_can_scope_to_its_single_global_owner():
    issue = "Humidity exposure: verify the duration."
    merged = merge_chunk_extractions([
        extraction(requirement("req_005"), issues=[issue]),
        extraction(requirement("req_009"), issues=[issue]),
    ])
    assert merged.review_issues == [f"req_001: {issue}"]


def test_duplicate_names_within_one_chunk_are_not_used_to_scope_generic_issue():
    issue = "Humidity exposure: verify the duration."
    merged = merge_chunk_extractions([
        extraction(requirement("req_005"), requirement("req_009"), issues=[issue]),
    ])
    assert merged.review_issues == [issue]
