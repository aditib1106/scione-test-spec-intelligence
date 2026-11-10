"""An unissued alternative class never borrows a primary method's criterion."""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_conditional_source_notes import FUTURE, OPTIONAL, OPTIONAL_REQUEST, PRIMARY, PRIMARY_CLASS
from test_conditional_source_notes import fixture as source_fixture

from scione.matching.models import RequirementCandidates
from scione.matching.optional_routes import optional_route_check
from scione.specifications.conditional_notes import conditional_route_proofs
from scione.structured.models import AtomicClaim, NormalizedRequirement


def fixture(selected=None):
    source_run, document = source_fixture(None)
    row = source_run.extraction.requirements[0]
    future = AtomicClaim(
        id="future_class", parameter_id=None, label="Alternative acceptance class", kind="scope",
        operator="text", text_value=FUTURE, raw_text=FUTURE, source_pages=[1],
        normalization_state="unresolved",
    )
    primary = AtomicClaim(
        id="primary_class", parameter_id="adhesion_class", label="Adhesion class",
        kind="criterion", operator="one_of", values=[0, 1], raw_text=PRIMARY_CLASS,
        source_pages=[1],
    )
    request = AtomicClaim(
        id="optional_request", parameter_id=None, label="Optional route", kind="scope",
        operator="text", text_value=OPTIONAL_REQUEST, raw_text=OPTIONAL_REQUEST, source_pages=[1],
        normalization_state="unresolved",
    )
    source = RequirementCandidates(
        requirement=row,
        normalized=NormalizedRequirement(
            requirement_id=row.id, test_name=row.original_test_name,
            claims=[future, primary, request],
        ),
    )
    run = SimpleNamespace(
        input_snapshot=SimpleNamespace(extraction_run_id=source_run.run_id),
        policy_snapshot={"verified_optional_route_proofs": conditional_route_proofs(
            source_run, document
        )},
        review_snapshot={"report_workspace": {"mappings": {
            row.id: {"selected_method_codes": selected}
        }}},
    )
    return run, source, future, primary, request


@pytest.mark.parametrize("selected", [None, [PRIMARY]])
def test_proved_inactive_route_is_informational_without_filling_its_class(selected):
    run, source, future, primary, request = fixture(selected)
    original = source.model_dump_json()
    for claim in (future, request):
        check = optional_route_check(run, source, claim)
        assert check.status == "informational"
        assert check.capability_claim_ids == []
        assert check.declared is None
        assert check.requirement_claim == claim
    assert optional_route_check(run, source, primary) is None
    assert source.model_dump_json() == original


@pytest.mark.parametrize("selected", [[OPTIONAL], [PRIMARY, OPTIONAL]])
def test_selecting_alternative_preserves_missing_class_and_forbids_primary_class_borrow(selected):
    run, source, future, primary, request = fixture(selected)
    for claim in (future, primary, request):
        check = optional_route_check(run, source, claim)
        assert check.status == "unknown"
        assert check.capability_claim_ids == []
        assert check.declared is None
    assert primary.values == [0, 1]


def test_manual_other_method_cannot_borrow_primary_class_even_when_its_role_is_unknown():
    run, source, _, primary, _ = fixture([OPTIONAL])
    source.requirement.requested_methods[1].role = "alternative"
    check = optional_route_check(run, source, primary)
    assert check.status == "unknown"
    assert check.capability_claim_ids == []


@pytest.mark.parametrize("mutation", [
    "missing_proof", "stale_source", "changed_requirement", "unlocated_quote",
    "unknown_role", "empty_selection", "unknown_selection", "mixed_quantity", "primary_future",
    "numeric_atom", "unit_atom", "extra_tool",
])
def test_unproved_or_mixed_conditional_claims_never_become_information_only(mutation):
    run, source, future, _, _ = fixture()
    if mutation == "missing_proof":
        run.policy_snapshot = {}
    elif mutation == "stale_source":
        run.input_snapshot.extraction_run_id = "other_run"
    elif mutation == "changed_requirement":
        source.requirement.original_test_name = "Changed source"
    elif mutation == "unlocated_quote":
        future.raw_text = FUTURE + " An invented clause."
    elif mutation == "unknown_role":
        source.requirement.requested_methods[1].role = "alternative"
    elif mutation == "empty_selection":
        run.review_snapshot["report_workspace"]["mappings"]["req_001"]["selected_method_codes"] = []
    elif mutation == "unknown_selection":
        run.review_snapshot["report_workspace"]["mappings"]["req_001"]["selected_method_codes"] = [
            "ISO 99999"
        ]
    elif mutation == "mixed_quantity":
        future.raw_text = FUTURE.rstrip(".") + " at 60 °C."
    elif mutation == "numeric_atom":
        future.operator = "="
        future.value = Decimal("999")
    elif mutation == "unit_atom":
        future.unit = "°C"
    elif mutation == "extra_tool":
        future.raw_text = FUTURE.rstrip(".") + " using a blue jig."
        source.requirement.evidence.append(
            source.requirement.evidence[-1].model_copy(update={"quote": future.raw_text})
        )
        from hashlib import sha256
        proof = deepcopy(run.policy_snapshot["verified_optional_route_proofs"]["req_001"])
        proof["source_requirement_sha256"] = sha256(
            source.requirement.model_dump_json().encode()
        ).hexdigest()
        run.policy_snapshot["verified_optional_route_proofs"]["req_001"] = proof
    elif mutation == "primary_future":
        future.raw_text = FUTURE.replace("ASTM", "ISO")
        source.requirement.evidence.append(
            source.requirement.evidence[-1].model_copy(update={"quote": future.raw_text})
        )
        # Even a trusted route proof is not permission to classify the primary
        # method's own missing criterion as an inactive alternative.
        from hashlib import sha256
        proof = deepcopy(run.policy_snapshot["verified_optional_route_proofs"]["req_001"])
        proof["source_requirement_sha256"] = sha256(
            source.requirement.model_dump_json().encode()
        ).hexdigest()
        run.policy_snapshot["verified_optional_route_proofs"]["req_001"] = proof
    check = optional_route_check(run, source, future)
    assert check.status == "unknown"
    assert check.capability_claim_ids == []
