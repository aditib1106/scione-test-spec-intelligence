"""Preparation quantities remain structured without claiming scientific support."""

from decimal import Decimal

import pytest

from scione.specifications.models import CustomerRequirement
from scione.structured import compare_requirement, normalize_requirement
from scione.structured.models import AtomicClaim
from scione.structured.planning import planning_quantities


def test_allocations_and_submitted_lots_keep_each_count_and_context():
    raw = (
        "Submit two independently mixed coating lots. "
        "Assign four to baseline checks, four to thermal cycling and four to dispute retention."
    )
    source = CustomerRequirement(
        id="req_counts",
        original_test_name="Coating tests",
        conditions=[dict(name="Sample plan", raw_value=raw)],
        evidence=[dict(page=1, quote=raw)],
    )
    normalized = normalize_requirement(source)
    assert [(c.parameter_id, c.value, c.source_context) for c in normalized.claims] == [
        ("submitted_lot_count", Decimal(2), "independently mixed coating lots"),
        ("sample_allocation", Decimal(4), "baseline checks"),
        ("sample_allocation", Decimal(4), "thermal cycling"),
        ("sample_allocation", Decimal(4), "dispute retention"),
    ]
    checks = compare_requirement(normalized, [], "unknown").checks
    assert checks[0].status == "unknown"
    assert all(c.status == "informational" for c in checks[1:])


@pytest.mark.parametrize(
    "raw",
    [
        "Assign four to baseline checks at 31 °C and four to storage.",
        "Assign four to steel and four to aluminium.",
        "Submit two uranium lots.",
        "Submit two coating lots at 31 °C.",
        "Assign -3 to baseline checks and three to thermal cycling.",
        "Assign 2.5 to baseline checks and three to thermal cycling.",
    ],
)
def test_unknown_materials_operating_demands_and_invalid_counts_remain_uninterpreted(raw):
    assert planning_quantities(raw) == []


@pytest.mark.parametrize("identifier", ["specimen_count", "lot_count"])
def test_source_classifier_proves_nominal_count_reclassification_without_rewriting_source(
    identifier,
):
    raw = "Submit two independently mixed coating lots."
    atom = AtomicClaim(
        id="native_count",
        parameter_id=identifier,
        label="Specimens",
        kind="condition",
        operator="=",
        value=2,
        unit="lots",
        raw_text=raw,
        source_context="independently mixed coating lots",
        source_pages=[1],
    )
    source = CustomerRequirement(
        id="req_planning",
        original_test_name="Coating test",
        atomic_claims=[atom],
        conditions=[dict(name="Sample plan", raw_value=raw)],
        evidence=[dict(page=1, quote=raw)],
    )
    before = source.model_dump_json()
    normalized = normalize_requirement(source)
    assert len(normalized.claims) == 1
    claim = normalized.claims[0]
    assert claim.parameter_id == "submitted_lot_count"
    assert claim.unit == "lots" and claim.value == 2 and claim.raw_text == raw
    assert claim.normalization_state == "parsed"
    assert normalized.normalization_actions[0]["original_parameter_id"] == identifier
    assert compare_requirement(normalized, [], "supported").checks[-1].status == "informational"
    assert source.model_dump_json() == before


@pytest.mark.parametrize(
    "change",
    [
        {"value": Decimal(3)},
        {"source_pages": [2]},
        {
            "raw_text": "Submit two independently mixed coating lots at 31 °C.",
        },
        {"unit": "°C"},
    ],
)
def test_count_reclassification_cannot_repair_wrong_count_unit_page_or_operating_scope(change):
    raw = "Submit two independently mixed coating lots."
    atom = AtomicClaim(
        id="native_count",
        parameter_id="specimen_count",
        label="Specimens",
        operator="=",
        value=2,
        unit="lots",
        raw_text=raw,
        source_context="independently mixed coating lots",
        source_pages=[1],
    ).model_copy(update=change)
    source = CustomerRequirement(
        id="req_planning",
        original_test_name="Coating test",
        atomic_claims=[atom],
        evidence=[dict(page=1, quote=raw)],
    )
    normalized = normalize_requirement(source)
    assert normalized.claims[0].normalization_state == "unresolved"
    assert not normalized.normalization_actions


@pytest.mark.parametrize("basis", ["per lot", "From each lot", "For each lot"])
def test_owned_allocation_basis_alias_does_not_turn_a_sample_plan_into_a_capability(basis):
    raw = (
        "Assign three to baseline checks, three to thermal cycling and three to dispute retention."
    )
    atom = AtomicClaim(
        id="native_allocation",
        parameter_id="panel_count",
        label="Panels",
        operator="=",
        value=3,
        unit="panels",
        raw_text=raw,
        source_context=f"{basis}; thermal cycling",
        source_pages=[1],
    )
    source = CustomerRequirement(
        id="req_allocation",
        original_test_name="Coating test",
        atomic_claims=[atom],
        evidence=[dict(page=1, quote=raw)],
    )
    before = source.model_dump_json()
    normalized = normalize_requirement(source)
    assert normalized.claims[0].parameter_id == "sample_allocation"
    assert normalized.claims[0].normalization_state == "parsed"
    assert normalized.claims[0].source_context == atom.source_context
    assert normalized.claims[0].unit == "panels"
    assert compare_requirement(normalized, [], "supported").checks[-1].status == "informational"
    assert source.model_dump_json() == before


@pytest.mark.parametrize(
    "basis", ["From each batch", "From each lot at 30 °C", "From each lot except aluminium"]
)
def test_allocation_alias_cannot_absorb_an_unknown_basis_or_additional_condition(basis):
    from scione.structured.planning import canonical_planning_parameter

    atom = AtomicClaim(
        id="native_allocation",
        parameter_id="panel_count",
        label="Panels",
        operator="=",
        value=3,
        unit="panels",
        raw_text="Assign three to baseline checks and three to thermal cycling.",
        source_context=f"{basis}; thermal cycling",
        source_pages=[1],
    )
    assert canonical_planning_parameter(atom) is None
