"""Source-local multilingual facts keep numbers, units and scientific identities."""

from decimal import Decimal

import pytest

from scione.specifications.models import CustomerRequirement
from scione.structured import AtomicClaim, normalize_requirement


def normalized(raw, parameter, *, unit, value=None, minimum=None, maximum=None, operator="=",
               kind="condition", context=None):
    claim = AtomicClaim(
        id="supplied", parameter_id=parameter, label=parameter, kind=kind,
        operator=operator, value=value, minimum=minimum, maximum=maximum,
        unit=unit, raw_text=raw, source_pages=[1],
    )
    requirement = CustomerRequirement(
        id="req_source", original_test_name="Source test",
        conditions=[{"name": "Source", "raw_value": raw}] if kind == "condition" else [],
        acceptance_criteria=[{"name": "Source", "raw_value": raw}] if kind == "criterion" else [],
        evidence=[{"page": 1, "quote": context or raw}], atomic_claims=[claim],
    )
    return normalize_requirement(requirement)


def test_span_canonicalization_preserves_cjk_literal_numbers_and_operator():
    result = normalized(
        "任何单根芯样不得低于 33.0 MPa", "compressive_strength", unit="MPa", value="33.0",
        operator=">=", kind="criterion", context="任何单根芯样不得\n低于 33.0 MPa",
    )
    assert result.claims[0].normalization_state == "parsed"
    assert result.claims[0].value == Decimal("33.0")


@pytest.mark.parametrize("unit,value,expected", [("根", "2", "parsed"),
                                               ("块", "3", "parsed"),
                                               ("根", "3", "unresolved")])
def test_chinese_count_value_cannot_borrow_a_different_classifier(unit, value, expected):
    result = normalized("从三块试板中各取两根芯样", "custom_count", unit=unit, value=value)
    assert result.claims[0].normalization_state == expected


@pytest.mark.parametrize("maximum,expected", [("1020", "parsed"), ("1.020", "unresolved")])
def test_spanish_thousands_supersedes_the_legacy_decimal_reading(maximum, expected):
    result = normalized(
        "La resistencia máxima del ensayo es 1.020 MPa", "tensile_strength",
        unit="MPa", value=maximum, kind="criterion",
    )
    assert result.claims[0].normalization_state == expected


def test_spanish_source_chain_preserves_both_original_range_endpoints():
    result = normalized(
        "Cada probeta válida deberá mostrar 860 ≤ Rm ≤ 1.020 MPa", "tensile_strength",
        unit="MPa", minimum="860", maximum="1020", operator="range", kind="criterion",
    )
    assert result.claims[0].normalization_state == "parsed"


def test_german_decimal_is_preserved_without_adding_a_false_five_millimetre_limit():
    result = normalized(
        "Der Mittelwert darf 1,5 mm nicht überschreiten", "coating_creep", unit="mm",
        value="1.5", operator="<=", kind="criterion",
    )
    assert result.claims[0].normalization_state == "parsed"
    assert not any(claim.value == 5 for claim in result.claims)


@pytest.mark.parametrize("parameter,raw,unit", [
    ("cycles", "Use 3 panels", "panels"),
    ("adhesion_class", "Detached area <= 5 %", "%"),
    ("illuminant", "Lighting category D65", None),
    ("measurement_resolution", "Resolution 5 mm²", "mm"),
])
def test_incompatible_quantity_scale_or_identifier_is_retained_unresolved(parameter, raw, unit):
    result = normalized(raw, parameter, unit=unit, value="5" if parameter != "cycles" else "3")
    assert result.claims[0].normalization_state == "unresolved"
    assert result.claims[0].availability == "unknown"


def test_unsupported_citation_number_change_does_not_gain_cjk_whitespace_approval():
    result = normalized(
        "温度为 31 °C", "temperature", unit="°C", value="31", context="温度为\n13 °C",
    )
    assert result.claims[0].normalization_state == "unresolved"
