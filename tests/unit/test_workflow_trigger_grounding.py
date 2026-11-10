"""Explicit workflow triggers cannot masquerade as specimen acceptance limits."""

from decimal import Decimal

import pytest

from scione.specifications.models import CustomerRequirement
from scione.structured import AtomicClaim, compare_requirement, normalize_requirement


@pytest.mark.parametrize("context", [
    "review trigger, not a hard acceptance cutoff",
    "rejection threshold, not an acceptance lower bound",
    "failure trigger requiring an engineering decision",
    "failure observation, not an acceptance condition",
    "not a hard acceptance requirement",
    "复核触发条件，非验收阈值",
])
def test_trigger_keeps_its_original_value_but_cannot_support_an_acceptance_decision(context):
    raw = "Crack length greater than 2 mm requires rejection review."
    atom = AtomicClaim(
        id="model_trigger", parameter_id="crack_length", label="Crack length",
        kind="criterion", operator=">", value=Decimal(2), unit="mm",
        raw_text=raw, source_context=context, source_pages=[1],
    )
    requirement = CustomerRequirement(
        id="req_trigger", original_test_name="Crack inspection",
        acceptance_criteria=[{"name": "Source wording", "raw_value": raw}],
        evidence=[{"page": 1, "quote": raw}], atomic_claims=[atom],
    )
    normalized = normalize_requirement(requirement)
    retained = normalized.claims[0]
    assert retained.value == 2 and retained.operator == ">"
    assert retained.normalization_state == "unresolved" and retained.availability == "unknown"
    assert atom.normalization_state == "parsed"  # Machine artifact was not rewritten.
    declaration = atom.model_copy(update={"operator": "range", "value": None,
                                           "minimum": Decimal(0), "maximum": Decimal(10)})
    assert compare_requirement(normalized, [declaration], method_status="supported").status != (
        "fully_matched"
    )


def test_normal_acceptance_threshold_is_not_relabelled_as_a_workflow_trigger():
    raw = "Thickness shall be at least 45 µm."
    atom = AtomicClaim(
        id="model_limit", parameter_id="film_thickness", label="Film thickness",
        kind="criterion", operator=">=", value=Decimal(45), unit="µm",
        raw_text=raw, source_context="minimum acceptance limit", source_pages=[1],
    )
    requirement = CustomerRequirement(
        id="req_acceptance", original_test_name="Film thickness",
        acceptance_criteria=[{"name": "Acceptance", "raw_value": raw}],
        evidence=[{"page": 1, "quote": raw}], atomic_claims=[atom],
    )
    assert normalize_requirement(requirement).claims[0].normalization_state == "parsed"


@pytest.mark.parametrize("parameter,parsed", [
    ("rockwell_hardness", False), ("rockwell_spread", True),
])
def test_max_minus_min_spread_does_not_use_an_absolute_hardness_policy(parameter, parsed):
    raw = "Maximum-minus-minimum spread shall be <= 4 HRC."
    claim = AtomicClaim(
        id="model_spread", parameter_id=parameter, label="Hardness result",
        kind="criterion", operator="<=", value=Decimal(4), unit="HRC", raw_text=raw,
        source_context="Maximum-minus-minimum spread", source_pages=[1],
    )
    requirement = CustomerRequirement(
        id="req_spread", original_test_name="Hardness uniformity",
        acceptance_criteria=[{"name": "Acceptance", "raw_value": raw}],
        evidence=[{"page": 1, "quote": raw}], atomic_claims=[claim],
    )
    normalized = normalize_requirement(requirement)
    assert (normalized.claims[0].normalization_state == "parsed") is parsed
    assert normalized.claims[0].value == 4
