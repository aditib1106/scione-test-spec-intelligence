"""Legacy retention parsing must not invent gloss or a nominal rejection limit."""

from decimal import Decimal

from scione.specifications.models import CustomerRequirement
from scione.structured.models import AtomicClaim
from scione.structured.normalization import normalize_requirement


def test_dimensional_retention_keeps_native_directional_facts_without_gloss_copies():
    first = "MD retention must remain at least 97.0%"
    second = "TD retention at least 96.0%"
    upper = "a value over 102.0% in either direction is also unacceptable"
    raw = f"{first} and {second}; {upper}."
    atoms = [
        AtomicClaim(
            id=f"native_{index}",
            parameter_id="length_retention",
            label="Length retention",
            kind="criterion",
            stage="after_cycle",
            operator=op,
            value=Decimal(value),
            unit="%",
            raw_text=quote,
            source_context=direction,
            source_pages=[1],
        )
        for index, (quote, op, value, direction) in enumerate(
            [
                (first, ">=", "97", "MD"),
                (second, ">=", "96", "TD"),
                (upper, "<=", "102", "either direction"),
            ]
        )
    ]
    source = CustomerRequirement(
        id="req_dimension",
        original_test_name="Dimensional stability of coated rolls",
        acceptance_criteria=[{"name": "Retention", "raw_value": raw}],
        evidence=[{"page": 1, "quote": raw}],
        atomic_claims=atoms,
    )
    normalized = normalize_requirement(source)
    assert not any(claim.parameter_id == "gloss_retention" for claim in normalized.claims)
    for original in atoms:
        assert any(
            claim.parameter_id == "length_retention"
            and claim.operator == original.operator
            and claim.value == original.value
            and claim.source_context == original.source_context
            for claim in normalized.claims
        )
    assert not any(
        claim.value == Decimal(102) and claim.operator == "=" for claim in normalized.claims
    )
    assert any(upper in claim.raw_text for claim in normalized.claims)


def test_generic_retention_and_rejection_threshold_remain_unresolved():
    raw = "Retention at least 97%; a value over 102% is unacceptable."
    source = CustomerRequirement(
        id="req_retention",
        original_test_name="Unspecified retention",
        acceptance_criteria=[{"name": "Retention", "raw_value": raw}],
        evidence=[{"page": 1, "quote": raw}],
    )
    claims = normalize_requirement(source).claims
    assert all(claim.normalization_state == "unresolved" for claim in claims)
    assert all(claim.parameter_id is None for claim in claims)


def test_explicit_gloss_family_can_still_structure_a_real_gloss_retention_limit():
    raw = "Each location must retain at least 90% of its own original value."
    source = CustomerRequirement(
        id="req_gloss",
        original_test_name="60 degree specular gloss",
        acceptance_criteria=[{"name": "Retention", "raw_value": raw}],
        evidence=[{"page": 1, "quote": raw}],
    )
    claims = normalize_requirement(source).claims
    assert any(
        claim.parameter_id == "gloss_retention"
        and claim.value == Decimal(90)
        and claim.operator == ">="
        for claim in claims
    )
