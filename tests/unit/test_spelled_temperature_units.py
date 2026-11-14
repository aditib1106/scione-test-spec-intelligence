"""Full thermal unit words must not be mistaken for an angular degree."""

from decimal import Decimal

import pytest

from scione.parameters.units import source_numbers_for_unit, unit_is_stated
from scione.specifications.models import CustomerRequirement
from scione.structured.lexical_grounding import source_quantities
from scione.structured.models import AtomicClaim
from scione.structured.normalization import normalize_requirement


@pytest.mark.parametrize("text,unit,value", [
    ("water at 60 degrees Celsius", "°C", 60),
    ("air at 40 degree Celsius", "°C", 40),
    ("water at 60 DEGREES CELSIUS", "°C", 60),
    ("air at 104 degrees Fahrenheit", "°F", 104),
])
def test_spelled_temperature_is_grounded_without_becoming_an_angle(text, unit, value):
    assert unit_is_stated(unit, text)
    assert source_numbers_for_unit(unit, text) == {Decimal(value)}
    assert not unit_is_stated("°", text)
    assert source_numbers_for_unit("°", text) == set()
    quantities = source_quantities(text)
    assert [(row.unit, row.value) for row in quantities] == [(unit, Decimal(value))]


@pytest.mark.parametrize("parameter,raw,value", [
    ("water_temperature", "During exposure use water at 60 degrees Celsius.", 60),
    ("air_temperature", "During exposure maintain air at 40 degrees Celsius.", 40),
])
def test_native_temperature_retains_identity_and_exact_number(parameter, raw, value):
    claim = AtomicClaim(
        id="native_temperature", parameter_id=parameter, label="Temperature",
        stage="exposure", operator="=", value=value, unit="°C", raw_text=raw,
        source_pages=[1],
    )
    requirement = CustomerRequirement(
        id="req_temperature", original_test_name="Customer test", atomic_claims=[claim],
        evidence=[{"page": 1, "quote": raw}],
    )
    normalized = normalize_requirement(requirement)
    native = normalized.claims[0]
    assert native.parameter_id == parameter
    assert native.normalization_state == "parsed"
    assert native.value == value
    assert native.raw_text == raw


def test_bare_angular_degrees_remain_supported():
    text = "Use a measurement angle of 60 degrees."
    assert unit_is_stated("°", text)
    assert source_numbers_for_unit("°", text) == {Decimal(60)}
    assert not unit_is_stated("°C", text)
    assert [(row.unit, row.value) for row in source_quantities(text)] == [("°", Decimal(60))]
