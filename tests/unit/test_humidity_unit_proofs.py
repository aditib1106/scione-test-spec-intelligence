"""RH recovery needs exact owned source evidence; a plain percentage is insufficient."""

from copy import deepcopy
from decimal import Decimal

import pytest
from test_semantic_matching import claim, proposal, snapshot
from test_semantic_wording import compile_case

from scione.matching.metadata import canonical_alignment_pair
from scione.matching.parameter_policy import source_proved_humidity_unit
from scione.specifications.models import CustomerRequirement, RequirementEvidence

RAW = "Allow seven days at 23 ± 2 °C and 50 ± 5% RH before initial readings."


def humidity(raw=RAW, **changes):
    values = {
        "parameter_id": "relative_humidity",
        "label": "Relative humidity",
        "stage": "conditioning",
        "value": Decimal(50),
        "tolerance": Decimal(5),
        "unit": "%",
        "raw_text": raw,
        "source_pages": [1],
    }
    values.update(changes)
    return claim("req_claim_1", **values)


def source_for(requested, *, quote=None, page=1):
    return CustomerRequirement(
        id="req_001",
        original_test_name="Customer humidity condition",
        evidence=[RequirementEvidence(page=page, quote=quote or requested.raw_text)],
        atomic_claims=[requested],
    )


@pytest.mark.parametrize(
    "raw",
    [
        RAW,
        "Condition at 50 ± 5 % RH.",
        "Relative humidity: 50 ± 5%.",
        "RH: 50 ± 5%.",
        "相对湿度为 50 ± 5%。",
    ],
)
def test_owned_literal_humidity_expression_recovers_only_the_comparison_unit(raw):
    requested = humidity(raw)
    original = requested.model_dump(mode="json")
    recovered, action = source_proved_humidity_unit(requested, source_for(requested))
    assert recovered.unit == "% RH"
    assert action["rule"] == "source_proved_relative_humidity_unit"
    assert action["source"] == "%"
    assert action["source_pages"] == [1]
    assert requested.model_dump(mode="json") == original
    assert recovered.model_dump(exclude={"unit"}) == requested.model_dump(exclude={"unit"})


@pytest.mark.parametrize(
    "changes",
    [
        {"parameter_id": "ratio", "label": "Retained fraction"},
        {"parameter_id": "temperature", "label": "Temperature"},
        {"parameter_id": "unknown_measurement", "label": "Unknown measurement"},
        {"label": "Unregistered field"},
        {"source_pages": []},
        {"source_pages": [2]},
        {"normalization_state": "unresolved"},
        {"availability": "unknown"},
        {"kind": "reporting"},
        {"value": Decimal(999)},
        {"tolerance": None},
        {"tolerance": Decimal(10)},
        {"operator": "<="},
        {"raw_text": "Condition at 50 ± 5 %."},
        {"raw_text": "Do not condition at 50 ± 5 % RH."},
        {"raw_text": "Relative humidity is not controlled; retention is 50 ± 5 %."},
        {"raw_text": "RH: 50%; a separate tolerance is 5%."},
        {"raw_text": "RH must be 50 ± 5% unless the customer asks otherwise."},
    ],
)
def test_percentage_never_becomes_humidity_without_complete_unambiguous_proof(changes):
    requested = humidity(**changes)
    recovered, action = source_proved_humidity_unit(requested, source_for(requested))
    assert recovered.unit == "%"
    assert action is None


def test_an_unowned_quote_cannot_supply_humidity_units():
    requested = humidity()
    recovered, action = source_proved_humidity_unit(
        requested, source_for(requested, quote="This test has no stated humidity.")
    )
    assert recovered.unit == "%"
    assert action is None


@pytest.mark.parametrize("low,expected", [("40", "supported"), ("46", "gap")])
def test_recovered_rh_still_requires_full_numeric_coverage_and_preserves_machine_snapshot(
    low, expected
):
    requested = humidity()
    declared = humidity(
        id="lab_claim_1",
        operator="range",
        value=None,
        tolerance=None,
        minimum=Decimal(low),
        maximum=Decimal(60),
        unit="% RH",
        raw_text=f"Declared RH operating range {low}–60 % RH.",
        source_pages=[],
    )
    data = snapshot(requested=[requested], declared=[declared])
    data.requirements[0].requirement = source_for(requested)
    original = deepcopy(data.model_dump(mode="json"))
    run, check = compile_case(data, proposal())
    assert check.status == expected
    assert check.requirement_claim.unit == "%"
    assert check.requirement_claim.value == 50
    assert check.requirement_claim.tolerance == 5
    assert data.model_dump(mode="json") == original
    assert run.input_snapshot.model_dump(mode="json") == original
    proofs = [
        item
        for item in run.validation_issues
        if item.code == "source_proved_metadata_canonicalization"
    ]
    assert any("source_proved_relative_humidity_unit" in item.reason for item in proofs)


def test_an_explicit_source_rh_unit_does_not_invent_a_lab_rh_declaration():
    requested = humidity(unit="% RH")
    declared = humidity(id="lab_claim_1", source_pages=[])
    first, second, actions = canonical_alignment_pair(
        requested, declared, source_for(requested), [declared]
    )
    assert first.unit == "% RH"
    assert second.unit == "%"
    assert not actions


@pytest.mark.parametrize("low,expected", [("40", "supported"), ("46", "gap")])
def test_named_structured_lab_percent_uses_rh_dimension_without_changing_values_or_units(
    low, expected,
):
    requested = humidity()
    declared = humidity(
        id="lab_claim_1", label="Panel conditioning relative humidity",
        operator="range", value=None, tolerance=None,
        minimum=Decimal(low), maximum=Decimal(60), unit="%",
        raw_text=f"Panel conditioning relative humidity: {low}–60 %.", source_pages=[],
    )
    data = snapshot(requested=[requested], declared=[declared])
    data.requirements[0].requirement = source_for(requested)
    before = data.model_dump_json()

    run, check = compile_case(data, proposal())

    assert check.status == expected
    assert run.output.items[0].claim_alignments[0].relation == "same_parameter"
    assert check.requirement_claim.unit == "%"
    assert check.requirement_claim.value == 50 and check.requirement_claim.tolerance == 5
    assert run.input_snapshot.capabilities[0].claims[0].unit == "%"
    assert data.model_dump_json() == before
    assert not any("incompatible dimensions" in issue.reason for issue in run.validation_issues)


@pytest.mark.parametrize("defect", [
    "plain_source_percent", "plain_lab_percent", "wrong_lab_label", "negated_lab_label",
    "changed_lab_value",
])
def test_humidity_dimension_bridge_needs_source_proof_and_explicit_matching_lab_record(defect):
    requested = humidity()
    declared = humidity(
        id="lab_claim_1", label="Panel conditioning relative humidity",
        operator="range", value=None, tolerance=None,
        minimum=Decimal(40), maximum=Decimal(60), unit="%",
        raw_text="Panel conditioning relative humidity: 40–60 %.", source_pages=[],
    )
    if defect == "plain_source_percent":
        requested.raw_text = "Condition at 50 ± 5 %."
        declared.unit = "% RH"
    elif defect == "plain_lab_percent":
        declared.raw_text = "Declared fraction: 40–60 %."
    elif defect == "wrong_lab_label":
        declared.label = "Fraction"
    elif defect == "negated_lab_label":
        declared.label = "Not relative humidity"
    else:
        declared.minimum = Decimal(41)
    data = snapshot(requested=[requested], declared=[declared])
    data.requirements[0].requirement = source_for(requested)
    before = data.model_dump_json()

    run, check = compile_case(data, proposal())

    assert run.output.items[0].claim_alignments[0].relation == "unknown"
    assert check.status == "unknown"
    assert data.model_dump_json() == before
