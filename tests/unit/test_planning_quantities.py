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


@pytest.mark.parametrize(
    "basis", ["per lot", "From each lot", "For each lot", "per lot, assigned to"]
)
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


@pytest.mark.parametrize("scope", ["baseline checks", "thermal cycling", "dispute retention"])
def test_literal_to_prefix_keeps_each_owned_planning_count_informational(scope):
    raw = (
        "Assign three to baseline checks, three to thermal cycling and three to dispute retention."
    )
    atom = AtomicClaim(
        id="native_allocation", parameter_id="panel_count", label="Panels",
        operator="=", value=3, unit="panels", raw_text=raw,
        source_context=f"to {scope}, from each lot", source_pages=[1],
    )
    source = CustomerRequirement(
        id="req_allocation", original_test_name="Coating test", atomic_claims=[atom],
        evidence=[dict(page=1, quote=raw)],
    )
    before = source.model_dump_json()
    result = normalize_requirement(source)
    claim = result.claims[0]
    assert claim.parameter_id == "sample_allocation"
    assert claim.normalization_state == "parsed"
    assert claim.value == 3 and claim.unit == "panels"
    assert claim.source_context == f"to {scope}, from each lot"
    assert compare_requirement(result, [], "supported").checks[-1].status == "informational"
    assert source.model_dump_json() == before


@pytest.mark.parametrize("context", [
    "to baseline checks except aluminium, from each lot",
    "to baseline checks at 30 °C, from each lot",
    "to baseline checks and thermal cycling, from each lot",
    "to to baseline checks, from each lot",
    "to baseline checks, from each batch",
])
def test_to_alias_cannot_discard_additional_scope_or_conditions(context):
    from scione.structured.planning import canonical_planning_parameter

    claim = AtomicClaim(
        id="count", parameter_id="panel_count", label="Panels", operator="=", value=3,
        unit="panels", raw_text="Assign three to baseline checks and three to thermal cycling.",
        source_context=context, source_pages=[1],
    )
    assert canonical_planning_parameter(claim) is None


@pytest.mark.parametrize("operator,value,minimum,raw", [
    (">=", 3, None, "Use at least three panels for the measurement."),
    (">=", 3, None, "At least three replicates are required."),
    ("range", None, 3, "Use three to five specimens for measurement."),
])
def test_scientific_replicate_count_is_never_reclassified_as_an_allocation(
    operator, value, minimum, raw
):
    from scione.structured.planning import canonical_planning_parameter

    claim = AtomicClaim(
        id="replicates", parameter_id="specimen_count", label="Replicates", kind="condition",
        operator=operator, value=value, minimum=minimum,
        maximum=5 if operator == "range" else None, unit="panels",
        raw_text=raw, source_context="baseline checks", source_pages=[1],
    )
    assert canonical_planning_parameter(claim) is None


def test_minimum_measurement_replicates_remain_a_scientific_capability_check():
    raw = "Use at least 3 specimens for the measurement."
    claim = AtomicClaim(
        id="replicates", parameter_id="specimen_count", label="Specimen count",
        kind="condition", operator=">=", value=3, unit="specimens", raw_text=raw,
        source_pages=[1],
    )
    row = CustomerRequirement(
        id="req_replicates", original_test_name="Adhesion", atomic_claims=[claim],
        evidence=[dict(page=1, quote=raw)],
    )
    result = normalize_requirement(row)
    assert result.claims[0].parameter_id == "specimen_count"
    assert result.claims[0].normalization_state == "parsed"
    check = compare_requirement(result, [], "supported").checks[-1]
    assert check.kind == "condition"
    assert check.status == "unknown"
    assert check.status != "informational"


@pytest.mark.parametrize("prefix,expected", [
    ("From each lot, coat nine panels; ", True),
    ("For each lot prepare nine panels; ", True),
    ("From each lot, coat nine panels. ", False),
    ("Coat nine panels; ", False),
    ("From each lot, coat nine panels pooled across both lots; ", False),
    ("From each lot, coat nine panels except aluminium; ", False),
])
def test_manual_planning_context_requires_explicit_local_per_lot_frame(prefix, expected):
    from scione.structured.planning import planning_context_is_source_grounded

    raw = "Assign three to baseline checks and three to thermal cycling."
    claim = AtomicClaim(
        id="allocation", parameter_id="panel_count", label="Panels", operator="=", value=3,
        unit="panels", raw_text=raw, source_context="baseline checks, from each lot",
        source_pages=[1],
    )
    assert planning_context_is_source_grounded(claim, prefix + raw) == expected
