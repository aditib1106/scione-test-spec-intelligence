"""Verbal ranges preserve source endpoints, units, offsets and unresolved syntax."""

from decimal import Decimal

import pytest

from scione.specifications.models import CustomerRequirement
from scione.structured.lexical_grounding import (
    grounded_source_numbers,
    source_operator_hints,
    source_quantities,
)
from scione.structured.models import AtomicClaim
from scione.structured.normalization import normalize_requirement


@pytest.mark.parametrize(
    "text,expected,unit",
    [
        ("Gloss shall be between 72 and 86 GU.", ["72", "86"], "GU"),
        ("Temperature from -20 to +70 °C", ["-20", "70"], "°C"),
        ("between 72 GU and 86 GU", ["72", "86"], "GU"),
        ("between two and six panels", ["2", "6"], "panels"),
        ("between 1.234567890123456789e-3 and 2.345678901234567891e-3 mm",
         ["0.001234567890123456789", "0.002345678901234567891"], "mm"),
        ("L’épaisseur doit être entre 1,2 et 1,5 mm", ["1.2", "1.5"], "mm"),
        ("La dureza estará entre 29 y 34 HRC", ["29", "34"], "HRC"),
        ("Prüfung zwischen -20 und 70 °C", ["-20", "70"], "°C"),
    ],
)
def test_verbal_range_binds_both_endpoints_to_one_source_expression(text, expected, unit):
    rows = source_quantities(text)
    assert [row.value for row in rows] == [Decimal(value) for value in expected]
    assert {row.role for row in rows} == {"range_endpoint"}
    assert {row.operator for row in rows} == {"range"}
    assert {row.unit for row in rows} == {unit}
    assert len({row.expression_span for row in rows}) == 1
    assert all(row.ambiguity is None for row in rows)
    assert source_operator_hints(text, unit=unit) == {"range"}
    for row in rows:
        assert text[slice(*row.number_span)] == row.raw_number
        assert text[slice(*row.unit_span)] == row.raw_unit


@pytest.mark.parametrize(
    "text,ambiguity",
    [
        ("between 72 GU and 86 mm", "mixed_units_in_range"),
        ("from 72 mPa to 86 MPa", "mixed_units_in_range"),
        ("between 1 mm and 2 cm", "mixed_units_in_range"),
        ("not between 72 and 86 GU", "negated_verbal_range"),
        ("shall not be between 72 and 86 GU", "negated_verbal_range"),
        ("Prüfung nicht zwischen -20 und 70 °C", "negated_verbal_range"),
        ("entre 72 et 86 GU", "unproved_range_language"),
        ("entre 29 y 34 HRC", "unproved_range_language"),
        ("zwischen -20 und 70 °C", "unproved_range_language"),
        ("between 72 und 86 GU", "unsupported_range_phrase"),
    ],
)
def test_ambiguous_range_syntax_or_units_cannot_certify_either_bound(text, ambiguity):
    rows = source_quantities(text)
    assert len(rows) == 2
    assert {row.ambiguity for row in rows} == {ambiguity}
    assert all(row.value is None for row in rows)
    assert grounded_source_numbers(text) == set()
    assert source_operator_hints(text) == set()


def test_verbatim_context_can_prove_language_but_cannot_supply_a_missing_range_endpoint():
    context = "L’épaisseur doit être mesurée entre 1,2 et 1,5 mm."
    assert grounded_source_numbers("entre 1,2 et 1,5 mm", context=context) == {
        Decimal("1.2"), Decimal("1.5")
    }
    assert source_quantities("1,5 mm", context=context)[0].role == "value"
    assert source_operator_hints("1,5 mm", context=context) == set()


@pytest.mark.parametrize(
    "text",
    [
        "between AX17 and 86 GU",
        "between D65 and 86 GU",
        "between 72 ± 2 and 86 GU",
        "between 1×10^3 and 86 GU",
        "between 1.2.3 and 86 GU",
    ],
)
def test_codes_tolerances_and_unsupported_arithmetic_are_not_lower_range_endpoints(text):
    assert source_operator_hints(text, unit="GU") == set()
    assert not any(
        row.role == "range_endpoint" and row.value is not None and row.ambiguity is None
        for row in source_quantities(text)
    )


def _normalized_range(raw, minimum, maximum, unit="GU"):
    claim = AtomicClaim(
        id="model_range", parameter_id="gloss", label="Gloss", kind="criterion",
        operator="range", minimum=Decimal(minimum), maximum=Decimal(maximum),
        unit=unit, raw_text=raw, source_pages=[1],
    )
    requirement = CustomerRequirement(
        id="req_verbal_range", original_test_name="Specular gloss",
        acceptance_criteria=[{"name": "Gloss", "raw_value": raw}],
        evidence=[{"page": 1, "quote": raw}], atomic_claims=[claim],
    )
    return normalize_requirement(requirement).claims[0]


def test_complete_model_range_is_grounded_without_rewriting_its_source_text():
    raw = "The gloss must be between 72 and 86 GU."
    result = _normalized_range(raw, "72", "86")
    assert result.normalization_state == "parsed"
    assert (result.minimum, result.maximum, result.operator) == (Decimal(72), Decimal(86), "range")
    assert result.raw_text == raw


@pytest.mark.parametrize(
    "raw,minimum,maximum",
    [
        ("between 72 and 86 GU", "71", "86"),
        ("between 72 and 86 GU", "72", "87"),
        ("between 10 and 20 GU or between 72 and 86 GU", "10", "86"),
        ("between 72 GU and 86 mm", "72", "86"),
        ("not between 72 and 86 GU", "72", "86"),
    ],
)
def test_model_range_cannot_repair_wrong_bounds_or_borrow_unrelated_endpoints(
    raw, minimum, maximum
):
    assert _normalized_range(raw, minimum, maximum).normalization_state == "unresolved"
