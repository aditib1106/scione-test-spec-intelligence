"""Source operators and enclosing contexts cannot be erased by normalization."""

from decimal import Decimal

import pytest

from scione.catalogs.models import LabCapability
from scione.specifications.models import (
    CustomerRequirement,
    RequirementEvidence,
    RequirementValue,
)
from scione.structured import (
    AtomicClaim,
    NormalizedRequirement,
    compare_requirement,
    normalize_capability,
    normalize_requirement,
)


def temperature(**updates):
    return AtomicClaim.model_validate(
        {
            "id": "temperature",
            "parameter_id": "temperature",
            "label": "Temperature",
            "operator": "=",
            "value": Decimal(23),
            "unit": "°C",
            "raw_text": "23 °C",
            "source_pages": [1],
            **updates,
        }
    )


def source_requirement(raw, *, atoms=(), context=None):
    return CustomerRequirement(
        id="req_source",
        original_test_name="Example test",
        conditions=[RequirementValue(name="Temperature", raw_value=raw, context=context)],
        evidence=[RequirementEvidence(page=1, quote=raw)],
        atomic_claims=list(atoms),
    )


def compare(request, declaration):
    result = compare_requirement(
        NormalizedRequirement(requirement_id="req_source", test_name="Example", claims=[request]),
        [declaration],
    )
    return result.checks[-1]


def test_identical_words_at_two_stages_remain_distinct_without_rewriting_quote():
    raw = "Temperature 23 °C"
    requirement = source_requirement(raw)
    requirement.conditions = [
        RequirementValue(name="Temperature", raw_value=raw, context="curing"),
        RequirementValue(name="Temperature", raw_value=raw, context="conditioning"),
    ]
    claims = normalize_requirement(requirement).claims
    assert [(claim.stage, claim.source_context) for claim in claims] == [
        ("curing", "curing"),
        ("conditioning", "conditioning"),
    ]
    assert all(claim.raw_text == raw and claim.source_pages == [1] for claim in claims)
    assert compare(claims[0], claims[1]).status == "unknown"


def test_separate_value_unit_field_uses_grounded_unit_without_rewriting_source():
    requirement = CustomerRequirement(
        id="req_value",
        original_test_name="Example",
        conditions=[RequirementValue(name="Temperature", raw_value="23", unit="°C")],
        evidence=[RequirementEvidence(page=1, quote="Operating temperature 23 degrees C")],
    )
    claim = normalize_requirement(requirement).claims[0]
    assert claim.parameter_id == "temperature"
    assert claim.value == Decimal(23)
    assert claim.unit == "°C"
    assert claim.raw_text == "23"
    assert claim.source_pages == [1]


def test_separate_unit_cannot_turn_source_hours_into_temperature():
    requirement = CustomerRequirement(
        id="req_value",
        original_test_name="Example",
        conditions=[RequirementValue(name="Temperature", raw_value="23", unit="°C")],
        evidence=[RequirementEvidence(page=1, quote="Condition for 23 hours")],
    )
    claims = normalize_requirement(requirement).claims
    assert all(claim.normalization_state == "unresolved" for claim in claims)
    assert not any(claim.parameter_id == "temperature" for claim in claims)


def test_unfamiliar_field_context_is_preserved_and_missing_lab_context_stays_unknown():
    normalized = normalize_requirement(source_requirement("23 °C", context="Aluminium substrate"))
    request = normalized.claims[0]
    assert request.source_context == "Aluminium substrate"
    assert request.raw_text == "23 °C"
    assert compare(request, temperature(id="lab")).status == "unknown"
    assert (
        compare(request, temperature(id="lab", source_context="Steel substrate")).status
        == "unknown"
    )
    assert (
        compare(request, temperature(id="lab", source_context="aluminium substrate")).status
        == "supported"
    )


def test_equivalent_named_stage_contexts_do_not_require_identical_spelling():
    request = temperature(stage="curing", source_context="curing")
    declaration = temperature(id="lab", stage="curing", source_context="Cure")
    assert compare(request, declaration).status == "supported"
    declaration.source_context = None
    assert compare(request, declaration).status == "supported"


def test_conflicting_source_stage_and_field_context_require_review():
    normalized = normalize_requirement(
        source_requirement("Conditioning temperature 23 °C", context="curing")
    )
    claim = next(item for item in normalized.claims if item.parameter_id == "temperature")
    assert claim.raw_text == "Conditioning temperature 23 °C"
    assert claim.source_context == "curing"
    assert claim.normalization_state == "unresolved"
    assert compare(claim, temperature(id="lab", stage="conditioning")).status == "unknown"


@pytest.mark.parametrize(
    ("source_operator", "model_operator"), [("<=", ">="), (">=", "<="), ("<=", "=")]
)
def test_model_operator_cannot_reverse_or_drop_an_explicit_source_bound(
    source_operator, model_operator
):
    raw = f"Temperature must be {source_operator} 23 °C"
    supplied = temperature(operator=model_operator, raw_text=raw)
    requirement = source_requirement(raw, atoms=[supplied])
    normalized = normalize_requirement(requirement)
    quarantined = next(claim for claim in normalized.claims if claim.parameter_id is None)
    assert quarantined.operator == model_operator
    assert quarantined.normalization_state == "unresolved"
    assert quarantined.availability == "unknown"
    parsed = [claim for claim in normalized.claims if claim.normalization_state == "parsed"]
    assert parsed and all(claim.operator == source_operator for claim in parsed)
    assert requirement.atomic_claims[0].operator == model_operator
    assert requirement.atomic_claims[0].normalization_state == "parsed"


@pytest.mark.parametrize(
    ("raw", "operator"),
    [("Temperature at least 23 °C", ">="), ("Temperature may not exceed 23 °C", "<=")],
)
def test_wrong_operator_for_an_explicit_verbal_bound_is_quarantined(raw, operator):
    wrong = "<=" if operator == ">=" else ">="
    normalized = normalize_requirement(
        source_requirement(raw, atoms=[temperature(operator=wrong, raw_text=raw)])
    )
    assert normalized.claims[0].normalization_state == "unresolved"
    assert normalized.claims[0].parameter_id is None
    parsed = [claim for claim in normalized.claims if claim.normalization_state == "parsed"]
    assert parsed and all(claim.operator == operator for claim in parsed)


@pytest.mark.parametrize(
    ("raw", "operator"),
    [
        ("Temperature at least 23 °C", ">="),
        ("Temperature no more than 23 °C", "<="),
        ("Temperature may not exceed 23 °C", "<="),
    ],
)
def test_grounded_verbal_operator_has_one_consistent_numeric_fact(raw, operator):
    normalized = normalize_requirement(
        source_requirement(raw, atoms=[temperature(operator=operator, raw_text=raw)])
    )
    assert len(normalized.claims) == 1
    assert normalized.claims[0].operator == operator
    assert normalized.claims[0].normalization_state == "parsed"


def test_named_colour_threshold_operator_is_grounded_without_a_physical_unit_suffix():
    raw = "ΔE*ab <=1.0"
    claim = temperature(
        parameter_id="color_difference",
        label="Colour difference",
        kind="criterion",
        operator=">=",
        value=Decimal(1),
        unit="ΔE*ab",
        raw_text=raw,
    )
    requirement = source_requirement(raw, atoms=[claim])
    requirement.conditions = []
    requirement.acceptance_criteria = [RequirementValue(name="Colour difference", raw_value=raw)]
    normalized = normalize_requirement(requirement)
    assert normalized.claims[0].normalization_state == "unresolved"
    assert any(
        atom.parameter_id == "color_difference" and atom.operator == "<="
        for atom in normalized.claims
    )


def test_grounded_model_atom_inherits_field_context_without_duplicate_fallback():
    raw = "23 °C"
    normalized = normalize_requirement(
        source_requirement(raw, context="curing", atoms=[temperature(raw_text=raw)])
    )
    assert len(normalized.claims) == 1
    assert normalized.claims[0].stage == "curing"
    assert normalized.claims[0].source_context == "curing"
    assert normalized.claims[0].raw_text == raw


def test_explicit_lab_claims_are_authoritative_over_legacy_prose_parser():
    declaration = temperature(
        id="lab_water", stage="measurement", source_context="Water", value=Decimal(60)
    )
    capability = LabCapability(
        id="cap_explicit",
        canonical_test_id="water_jet",
        declared_constraints="Legacy draft: 23 °C only.",
        atomic_claims=[declaration],
    )
    claims = normalize_capability(capability)
    assert len(claims) == 1
    assert claims[0].id == "lab_water"
    assert claims[0].value == 60
    assert claims[0].source_context == "Water"


def test_specific_grounded_step_atoms_replace_generic_numeric_fallback_copies_only():
    raw = "step 1 at -12 °C for 45 min under nitrogen"
    source = source_requirement(raw)
    source.conditions.append(RequirementValue(name="Duration", raw_value=raw))
    source.evidence[0].quote = "Execute thermal-cycle stages in order: " + raw
    source.atomic_claims = [
        temperature(raw_text=raw, value=Decimal(-12), stage="thermal_cycle", sequence=1),
        temperature(
            id="duration", parameter_id="duration", label="Duration", raw_text=raw,
            value=Decimal(45), unit="min", stage="thermal_cycle", sequence=1,
        ),
    ]
    claims = normalize_requirement(source).claims
    numeric = [claim for claim in claims if claim.normalization_state != "unresolved"]
    assert [(claim.parameter_id, claim.stage, claim.sequence) for claim in numeric] == [
        ("temperature", "thermal_cycle", 1), ("duration", "thermal_cycle", 1),
    ]
    # Step wording not understood by the offline parser stays inspectable.
    unresolved = [claim for claim in claims if claim.normalization_state == "unresolved"]
    assert unresolved and all(claim.raw_text == raw for claim in unresolved)
    assert all("nitrogen" in claim.text_value for claim in unresolved)
    assert source.atomic_claims[0].id == "temperature"


@pytest.mark.parametrize("context, numeric_count", [("Water", 1), ("Nitrogen", 2)])
def test_more_specific_model_context_needs_literal_local_source_support(context, numeric_count):
    raw = "Water at 23 °C"
    normalized = normalize_requirement(
        source_requirement(raw, atoms=[temperature(raw_text=raw, source_context=context)])
    )
    assert len([claim for claim in normalized.claims if claim.parameter_id == "temperature"]) == (
        numeric_count
    )
    assert any(
        claim.normalization_state == "unresolved" and claim.raw_text == raw
        for claim in normalized.claims
    )


def test_model_step_contradicting_its_quotation_cannot_suppress_numeric_fallback():
    raw = "step 1 at 23 °C"
    source = source_requirement(
        raw, atoms=[temperature(raw_text=raw, stage="thermal_cycle", sequence=2)]
    )
    source.evidence[0].quote = "Execute thermal-cycle stages: " + raw
    claims = normalize_requirement(source).claims
    assert any(
        claim.parameter_id == "temperature" and claim.stage == "general" and claim.sequence is None
        for claim in claims
    )


def test_specific_model_step_cannot_erase_a_contradictory_named_source_stage():
    raw = "Conditioning step 1 at 23 °C"
    normalized = normalize_requirement(
        source_requirement(raw, atoms=[temperature(raw_text=raw, stage="curing", sequence=1)])
    )
    assert {claim.stage for claim in normalized.claims if claim.parameter_id == "temperature"} == {
        "curing", "conditioning",
    }


@pytest.mark.parametrize(
    "update", [{"value": Decimal(99)}, {"unit": "h"}, {"source_pages": [2]}]
)
def test_quarantined_specific_model_atoms_never_suppress_correct_numeric_fallback(update):
    raw = "step 1 at 23 °C"
    source = source_requirement(
        raw, atoms=[temperature(raw_text=raw, stage="thermal_cycle", sequence=1, **update)]
    )
    source.evidence[0].quote = "Execute thermal-cycle stages: " + raw
    claims = normalize_requirement(source).claims
    assert claims[0].normalization_state == "unresolved"
    assert any(
        claim.parameter_id == "temperature" and claim.value == Decimal(23) and claim.unit == "°C"
        for claim in claims[1:]
    )


def test_one_specific_model_step_cannot_hide_another_equal_quantity_in_the_same_clause():
    raw = "step 1 at 23 °C then step 2 at 23 °C"
    source = source_requirement(
        raw, atoms=[temperature(raw_text="step 1 at 23 °C", stage="thermal_cycle", sequence=1)]
    )
    source.evidence[0].quote = "Execute thermal-cycle stages: " + raw
    claims = normalize_requirement(source).claims
    # The fallback has no source offsets to distinguish the two equal readings.
    assert len([claim for claim in claims if claim.parameter_id == "temperature"]) == 3


def test_model_fact_from_a_different_step_span_does_not_suppress_this_clause():
    source = source_requirement(
        "step 2 at 23 °C",
        atoms=[temperature(raw_text="step 1 at 23 °C", stage="thermal_cycle", sequence=1)],
    )
    source.evidence[0].quote = "Thermal-cycle stages: step 1 at 23 °C; step 2 at 23 °C"
    claims = normalize_requirement(source).claims
    assert len([claim for claim in claims if claim.parameter_id == "temperature"]) == 2
