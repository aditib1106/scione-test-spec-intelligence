"""Only source-proved redundant axes can be canonicalized for comparison."""

from copy import deepcopy

import pytest

from scione.matching.metadata import canonical_alignment_pair
from scione.parameters import contexts_match
from scione.specifications.models import CustomerRequirement
from scione.structured.models import AtomicClaim


def thermal(*, context="cold step of each cycle", sequence=1, page=1):
    quote = "-20 °C for two hours"
    source = CustomerRequirement(
        id="req_cycle",
        original_test_name="Thermal cycling",
        evidence=[
            dict(
                page=page,
                quote="Repeat five cycles between -20 °C for two hours and 70 °C for two hours.",
            )
        ],
    )
    first = AtomicClaim(
        id="requested",
        parameter_id="temperature",
        label="Temperature",
        kind="condition",
        stage="exposure",
        sequence=sequence,
        operator="=",
        value=-20,
        unit="°C",
        raw_text=quote,
        source_context=context,
        source_pages=[1],
    )
    second = AtomicClaim(
        id="lab",
        parameter_id="temperature",
        label="Cold step temperature",
        kind="condition",
        stage="thermal_cycle",
        sequence=sequence,
        operator="range",
        minimum=-40,
        maximum=0,
        unit="°C",
        raw_text="Lab declaration.",
    )
    return first, second, source


def test_own_literal_cycle_quote_proves_phase_and_sequence_without_changing_original():
    first, second, source = thermal()
    before = deepcopy(first.model_dump())
    requested, declared, actions = canonical_alignment_pair(first, second, source, [second])
    assert requested.stage == "thermal_cycle"
    assert requested.source_context is None
    assert contexts_match(requested, declared)
    assert first.model_dump() == before
    assert any(a.get("verified_sequence") == 1 and a.get("source_quote_sha256") for a in actions)


@pytest.mark.parametrize(
    "change",
    [
        "wrong_sequence",
        "wrong_page",
        "extra_material",
        "extra_quantity",
        "statistic",
        "code",
        "missing_parent",
    ],
)
def test_phase_proof_does_not_erase_other_scope_or_repair_unproved_metadata(change):
    first, second, source = thermal()
    if change == "wrong_sequence":
        first.sequence = second.sequence = 2
    elif change == "wrong_page":
        source.evidence[0].page = 2
    elif change == "extra_material":
        first.source_context += " under nitrogen"
    elif change == "extra_quantity":
        first.source_context += " at 2 bar"
    elif change == "statistic":
        first.source_context += ", mean reading"
    elif change == "code":
        first.source_context += ", setup XQ-a"
    else:
        source.evidence[0].quote = "A different exposure at -20 °C for two hours."
    requested, declared, _actions = canonical_alignment_pair(first, second, source, [second])
    assert not contexts_match(requested, declared)


def test_metal_subject_is_explicit_on_both_records_not_inferred_from_missing_lab_data():
    first = AtomicClaim(
        id="r",
        parameter_id="temperature",
        label="Temperature",
        operator="=",
        value=140,
        unit="°C",
        stage="curing",
        raw_text="Actual metal temperature, not oven setting: 140 °C.",
        source_context="actual metal temperature, not oven setting",
        source_pages=[1],
    )
    source = CustomerRequirement(
        id="req_cure", original_test_name="Cure", evidence=[dict(page=1, quote=first.raw_text)]
    )
    second = first.model_copy(
        update=dict(
            id="l",
            source_context=None,
            label="Actual metal temperature",
            raw_text="Lab declaration.",
        )
    )
    requested, declared, actions = canonical_alignment_pair(first, second, source, [second])
    assert contexts_match(requested, declared) and actions
    second.label = "Temperature"
    requested, declared, _ = canonical_alignment_pair(first, second, source, [second])
    assert not contexts_match(requested, declared)


@pytest.mark.parametrize(
    "context", ["cure reference", "cure reference at actual metal temperature"]
)
def test_cure_reference_duration_needs_the_owned_object_and_same_configuration_lab_temperature(
    context,
):
    raw = "20 minutes at 140 ± 3 °C"
    source = CustomerRequirement(
        id="req_duration",
        original_test_name="Cure",
        evidence=[
            dict(
                page=1,
                quote="Actual metal temperature, not oven setting, is the cure reference: "
                + raw
                + ".",
            )
        ],
    )
    first = AtomicClaim(
        id="r",
        parameter_id="duration",
        label="Duration",
        stage="curing",
        operator="=",
        value=20,
        unit="min",
        raw_text=raw,
        source_context=context,
        source_pages=[1],
    )
    second = first.model_copy(
        update={"id": "l", "source_context": None, "raw_text": "Lab duration"}
    )
    temperature = AtomicClaim(
        id="object_temperature",
        parameter_id="temperature",
        label="Actual metal temperature",
        stage="curing",
        operator="range",
        minimum=100,
        maximum=180,
        unit="°C",
        raw_text="Actual metal temperature range",
    )
    a, b, actions = canonical_alignment_pair(first, second, source, [second, temperature])
    assert actions and contexts_match(a, b)
    temperature.availability = "unknown"
    a, b, _ = canonical_alignment_pair(first, second, source, [second, temperature])
    assert not contexts_match(a, b)
