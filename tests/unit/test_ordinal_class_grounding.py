"""Prefix ordinal units are source evidence, never method equivalence."""

from decimal import Decimal

import pytest

from scione.specifications.models import CustomerRequirement
from scione.structured.lexical_grounding import source_quantities
from scione.structured.models import AtomicClaim
from scione.structured.normalization import normalize_requirement


def normalized(raw, *, operator="one_of", values=(0, 1), value=None, **updates):
    claim = AtomicClaim(
        id="source_class",
        parameter_id="adhesion_class",
        label="Adhesion class",
        kind="criterion",
        operator=operator,
        values=list(values),
        value=value,
        raw_text=raw,
        source_pages=[1],
        unit=updates.pop("unit", "ISO class"),
        **updates,
    )
    source = CustomerRequirement(
        id="req_class",
        original_test_name="Cross-cut adhesion",
        acceptance_criteria=[{"name": "Class", "raw_value": raw}],
        evidence=[{"page": 1, "quote": raw}],
        atomic_claims=[claim],
    )
    return normalize_requirement(source).claims[0]


@pytest.mark.parametrize(
    "raw",
    [
        "only class 0 or 1 is acceptable",
        "ISO class 0/1",
        "Class 0 OR 1",
    ],
)
def test_prefix_class_lists_preserve_values_unit_and_literal_offsets(raw):
    quantities = source_quantities(raw)
    assert [quantity.value for quantity in quantities] == [Decimal(0), Decimal(1)]
    assert all(
        quantity.unit == "class" and quantity.operator == "one_of" for quantity in quantities
    )
    for quantity in quantities:
        assert raw[slice(*quantity.number_span)] == quantity.raw_number
        assert "class" in raw[slice(*quantity.unit_span)].lower()
    result = normalized(raw)
    assert result.normalization_state == "parsed"
    assert result.parameter_id == "adhesion_class"
    assert result.values == [Decimal(0), Decimal(1)]
    assert result.unit == "ISO class"


def test_prefix_class_range_and_comparator_are_not_discrete_or_inverted():
    result = normalized(
        "ISO class 0-1 before and after cycles",
        operator="range",
        values=(),
        minimum=Decimal(0),
        maximum=Decimal(1),
    )
    assert result.normalization_state == "parsed"
    assert result.operator == "range"
    assert (
        normalized("class <=1", operator="<=", values=(), value=Decimal(1)).normalization_state
        == "parsed"
    )
    assert (
        normalized("class <=1", operator=">=", values=(), value=Decimal(1)).normalization_state
        == "unresolved"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "not class 0 or 1",
        "reject class 0 or 1",
        "not accepted if class 0 or 1",
        "class 0 or 1 is not acceptable",
        "class 0 or 1 are prohibited",
        "except ISO class 0 or 1",
        "class <=0 or 1",
        "class 0 or 1 or 2",
        "class 1B",
        "classification 0 or 1",
    ],
)
def test_negated_partial_mixed_or_code_like_class_stays_unresolved(raw):
    assert normalized(raw).normalization_state == "unresolved"


def test_prefix_class_cannot_ground_an_unstated_member_or_other_quantity_unit():
    assert normalized("class 0 or 1", values=(0, 5)).normalization_state == "unresolved"
    quantities = source_quantities("ISO 60038:2021, class 0 or 1 at 23 °C")
    assert [(row.value, row.unit) for row in quantities] == [
        (Decimal(0), "class"),
        (Decimal(1), "class"),
        (Decimal(23), "°C"),
    ]


def test_single_prefix_class_retains_a_negative_source_warning():
    raw = "class 1 is not acceptable"
    quantities = source_quantities(raw)
    assert len(quantities) == 1
    assert quantities[0].operator_ambiguity == "negated_or_mixed_ordinal_class"
    assert normalized(raw, operator="=", values=(), value=1).normalization_state == "unresolved"


def test_named_class_without_a_separate_display_unit_keeps_operator_guards():
    accepted = normalized("adhesion class <=1", operator="<=", values=(), value=1, unit=None)
    assert accepted.normalization_state == "parsed"
    assert accepted.unit is None
    wrong = normalized("adhesion class <=1", operator=">=", values=(), value=1, unit=None)
    assert wrong.normalization_state == "unresolved"
    rejected = normalized("class 1 is not acceptable", operator="=", values=(), value=1, unit=None)
    assert rejected.normalization_state == "unresolved"
