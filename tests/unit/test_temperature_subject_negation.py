"""Explicitly excluded temperature subjects do not create spurious ambiguity."""

import pytest

from scione.parameters import evaluate_pair
from scione.structured.models import AtomicClaim


def claim(raw, **updates):
    return AtomicClaim.model_validate(
        dict(
            id="temperature",
            parameter_id="temperature",
            label="Temperature",
            kind="condition",
            stage="curing",
            operator="=",
            value="140",
            unit="°C",
            raw_text=raw,
        )
        | updates
    )


@pytest.mark.parametrize(
    "excluded",
    [
        "not oven setting",
        "rather than oven setting",
        "instead of oven setting",
        "而非炉温",
        "不是烘箱温度",
    ],
)
def test_explicit_metal_not_oven_source_has_one_subject(excluded):
    requested = claim(f"Actual metal temperature, {excluded}: 140 °C.")
    declared = claim("Specimen temperature 140 °C.")
    assert evaluate_pair(requested, declared).status == "supported"


def test_two_positive_subjects_or_a_conflicting_label_remain_unknown():
    requested = claim("Metal temperature and oven setting: 140 °C.")
    declared = claim("Specimen temperature 140 °C.")
    assert evaluate_pair(requested, declared).status == "unknown"
    conflict = claim(
        "Actual metal temperature, not oven setting: 140 °C.", label="Oven temperature"
    )
    assert evaluate_pair(conflict, declared).status == "unknown"


def test_negation_does_not_bridge_metal_and_oven_or_other_stages():
    requested = claim("Actual metal temperature, not oven setting: 140 °C.")
    oven = claim("Oven setting 140 °C.")
    assert evaluate_pair(requested, oven).status == "unknown"
    other_stage = claim("Specimen temperature 140 °C.", stage="conditioning")
    assert evaluate_pair(requested, other_stage).status == "unknown"
