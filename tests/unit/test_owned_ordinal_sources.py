"""An ordinal list is grounded by explicit source enumeration, not guessed steps."""

import pytest

from scione.specifications.models import CustomerRequirement, RequestedMethod, RequirementEvidence
from scione.structured.models import AtomicClaim
from scione.structured.normalization import known_source_fact_problem, normalize_requirement
from scione.structured.ordinal_sources import owned_ordinal_enumeration

SUMMARY = "ISO class 0-1 before and after cycles"
BODY = "If the ISO procedure is used, only class 0 or 1 is acceptable."


def source(body=BODY):
    return CustomerRequirement(
        id="req_ordinal",
        original_test_name="Adhesion",
        requested_methods=[RequestedMethod(code="ISO 60038:2021", role="primary")],
        evidence=[RequirementEvidence(page=1, quote=BODY + "\n" + SUMMARY)],
        atomic_claims=[
            AtomicClaim(
                id="native_class",
                parameter_id="adhesion_class",
                label="Adhesion class",
                kind="criterion",
                operator="one_of",
                values=[0, 1],
                raw_text=SUMMARY,
                source_pages=[1],
            )
        ],
    ).model_copy(update={"evidence": [RequirementEvidence(page=1, quote=body + "\n" + SUMMARY)]})


def test_compact_summary_can_be_grounded_by_the_same_owned_explicit_allowed_list():
    row = source()
    before = row.model_dump_json()
    proof = owned_ordinal_enumeration(row.atomic_claims[0], row)
    assert proof and proof["values"] == ["0", "1"]
    normalized = normalize_requirement(row)
    assert normalized.claims[0].normalization_state == "parsed"
    assert known_source_fact_problem(normalized.claims[0], row) is None
    assert proof in normalized.normalization_actions
    assert row.model_dump_json() == before


@pytest.mark.parametrize(
    "body",
    [
        "",
        "Class 0 or 1.",
        "The ASTM procedure accepts only class 0 or 1.",
        "If the ISO procedure is used, only class 0 or 2 is acceptable.",
        BODY + " If the ISO procedure is used, only class 0 or 2 is acceptable.",
    ],
)
def test_no_body_enumeration_or_conflicting_allowed_list_means_no_recovery(body):
    row = source(body)
    assert owned_ordinal_enumeration(row.atomic_claims[0], row) is None


def test_different_method_or_unowned_summary_cannot_borrow_the_source_list():
    row = source()
    row.requested_methods = [RequestedMethod(code="ASTM D1037-20", role="primary")]
    assert owned_ordinal_enumeration(row.atomic_claims[0], row) is None
    row = source()
    row.evidence = [RequirementEvidence(page=1, quote=BODY)]
    assert owned_ordinal_enumeration(row.atomic_claims[0], row) is None
