"""Supplied atoms and fallback atoms share unit, role and source-boundary guards."""

from decimal import Decimal

import pytest

from scione.specifications.models import CustomerRequirement
from scione.structured.models import NORMALIZER_VERSION, AtomicClaim
from scione.structured.normalization import normalize_requirement


def normalize(
    raw,
    *,
    parameter="temperature",
    value="20",
    unit="°C",
    operator="=",
    tolerance=None,
    minimum=None,
    maximum=None,
    with_model=True,
):
    atom = AtomicClaim(
        id="model",
        parameter_id=parameter,
        label=parameter,
        operator=operator,
        value=Decimal(value) if value is not None else None,
        unit=unit,
        tolerance=Decimal(tolerance) if tolerance is not None else None,
        minimum=Decimal(minimum) if minimum is not None else None,
        maximum=Decimal(maximum) if maximum is not None else None,
        raw_text=raw,
        source_pages=[1],
    )
    source = CustomerRequirement(
        id="req_numeric_guard",
        original_test_name="Film thickness / gloss assessment",
        conditions=[{"name": parameter, "raw_value": raw}],
        evidence=[{"page": 1, "quote": raw}],
        atomic_claims=[atom] if with_model else [],
    )
    result = normalize_requirement(source)
    assert atom.normalization_state == "parsed"  # The provider artifact stays unchanged.
    return result.claims


@pytest.mark.parametrize(
    "raw,parameter,value,unit",
    [
        ("Gloss <= 100 GU/s", "gloss", "100", "GU"),
        ("Area 100 µm²", "film_thickness", "100", "µm"),
        ("Area 100 µm^2", "film_thickness", "100", "µm"),
        ("Heating rate <= 20 degrees C/min", "temperature", "20", "°C"),
        ("Heating rate <= 20 ° C/min", "temperature", "20", "°C"),
        ("Step two cycles", "cycles", "2", "cycles"),
    ],
)
@pytest.mark.parametrize("with_model", [True, False])
def test_fallback_cannot_recreate_a_quarantined_quantity(raw, parameter, value, unit, with_model):
    claims = normalize(
        raw,
        parameter=parameter,
        value=value,
        unit=unit,
        operator="<=" if "<=" in raw else "=",
        with_model=with_model,
    )
    assert not any(
        claim.parameter_id == parameter
        and claim.value == Decimal(value)
        and claim.normalization_state != "unresolved"
        for claim in claims
    )
    assert any(claim.normalization_state == "unresolved" for claim in claims)


@pytest.mark.parametrize("raw", ["Temperature 20 degrees C", "Temperature 20 ° C"])
def test_full_celsius_aliases_retain_their_original_supported_meaning(raw):
    assert normalize(raw)[0].normalization_state == "parsed"
    assert any(
        claim.parameter_id == "temperature" and claim.value == 20
        for claim in normalize(raw, with_model=False)
    )


@pytest.mark.parametrize(
    "raw,parameter",
    [
        ("3 panels", "cycles"),
        ("三块样板", "measurement_locations"),
    ],
)
def test_an_omitted_intrinsic_count_unit_does_not_borrow_a_different_classifier(raw, parameter):
    claims = normalize(raw, parameter=parameter, value="3", unit=None)
    assert claims[0].normalization_state == "unresolved"
    assert not any(
        claim.parameter_id == parameter and claim.normalization_state != "unresolved"
        for claim in claims
    )


@pytest.mark.parametrize(
    "parameter,raw,expected_unit",
    [
        ("cycles", "3 cycles", "cycles"),
        ("measurement_locations", "3 locations", "locations"),
    ],
)
def test_an_intrinsic_unit_is_added_only_after_its_own_source_quantity_can_be_checked(
    parameter,
    raw,
    expected_unit,
):
    claim = normalize(raw, parameter=parameter, value="3", unit=None)[0]
    assert claim.normalization_state == "parsed" and claim.unit == expected_unit


@pytest.mark.parametrize("unit,value", [("根", "2"), ("块", "3")])
def test_custom_chinese_classifiers_keep_their_own_curated_source_unit(unit, value):
    claims = normalize("从三块试板中各取两根芯样", parameter="custom_count", value=value, unit=unit)
    assert claims[0].normalization_state == "parsed"


def test_filling_a_count_unit_does_not_prematurely_replace_its_source_stage():
    raw = "Measure 3 locations after cycling."
    atom = AtomicClaim(
        id="model",
        parameter_id="measurement_locations",
        label="Locations",
        value=3,
        operator="=",
        unit=None,
        raw_text=raw,
        source_pages=[1],
    )
    requirement = CustomerRequirement(
        id="req_stage_count",
        original_test_name="Gloss",
        conditions=[{"name": "Locations", "raw_value": raw, "context": "after_cycle"}],
        evidence=[{"page": 1, "quote": raw}],
        atomic_claims=[atom],
    )
    claim = normalize_requirement(requirement).claims[0]
    assert claim.normalization_state == "parsed" and claim.stage == "after_cycle"


@pytest.mark.parametrize("value,tolerance", [("2", None), ("20", None), ("20", "3")])
def test_nominal_temperature_and_tolerance_cannot_be_swapped_or_dropped(value, tolerance):
    claims = normalize("Temperature 20 ± 2 °C", value=value, tolerance=tolerance)
    assert claims[0].normalization_state == "unresolved"
    assert any(
        claim.parameter_id == "temperature" and claim.value == 20 and claim.tolerance == 2
        for claim in claims
    )


def test_a_complete_nominal_and_tolerance_pair_remains_grounded():
    claim = normalize("Temperature 20 ± 2 °C", tolerance="2")[0]
    assert claim.normalization_state == "parsed"
    assert claim.value == 20 and claim.tolerance == 2


def test_a_tolerance_cannot_be_borrowed_from_another_temperature():
    claims = normalize("Temperature 20 °C; another temperature 5 ± 2 °C", tolerance="2")
    assert claims[0].normalization_state == "unresolved"


def test_a_range_cannot_borrow_endpoints_from_two_different_source_ranges():
    claims = normalize(
        "Use 10–20 °C or 60–70 °C", value=None, operator="range", minimum="10", maximum="70"
    )
    assert claims[0].normalization_state == "unresolved"
    correct = normalize("Use 10–20 °C", value=None, operator="range", minimum="10", maximum="20")
    assert correct[0].normalization_state == "parsed"


def test_an_invented_unknown_unit_is_not_established_by_an_unrelated_number():
    assert (
        normalize("Temperature 20 °C", parameter="custom_quantity", unit="bananas")[
            0
        ].normalization_state
        == "unresolved"
    )
    assert (
        normalize("Temperature 20 °C", parameter="custom_quantity", unit=None)[
            0
        ].normalization_state
        == "unresolved"
    )
    explicit = normalize("Quantity 20 widgets", parameter="custom_quantity", unit="widgets")[0]
    assert explicit.normalization_state == "parsed" and explicit.unit == "widgets"


def test_a_literal_unknown_compound_unit_is_retained_in_full():
    claim = normalize("Rate <= 20 GU/s", parameter="gloss_rate", unit="GU/s", operator="<=")[0]
    assert claim.normalization_state == "parsed" and claim.unit == "GU/s"


@pytest.mark.parametrize(
    "raw,unit",
    [
        ("Relative humidity 50 % RH", "%"),
        ("Relative humidity 50 %", "% RH"),
    ],
)
def test_percent_spelling_aliases_are_scoped_to_registered_relative_humidity(raw, unit):
    assert (
        normalize(raw, parameter="relative_humidity", value="50", unit=unit)[0].normalization_state
        == "parsed"
    )
    wrong = normalize(raw, parameter="gloss_retention", value="50", unit=unit)
    if "% RH" in raw:
        assert wrong[0].normalization_state == "unresolved"
    old = normalize(raw, parameter="relative_humidity", value="50", unit=unit, with_model=False)
    assert any(
        claim.parameter_id == "relative_humidity"
        and claim.value == 50
        and claim.normalization_state == "parsed"
        for claim in old
    )


@pytest.mark.parametrize("raw", ["not not less than 5 °C", "不得不小于5 °C", "not <= 5 °C"])
def test_nested_or_negated_operator_cannot_be_reapproved_by_the_legacy_parser(raw):
    claims = normalize(raw, value="5", operator=">=")
    assert not any(
        claim.parameter_id == "temperature" and claim.normalization_state != "unresolved"
        for claim in claims
    )


@pytest.mark.parametrize(
    "raw,value,unit",
    [
        ("Un alargamiento a rotura de al menos 11 %", "11", "%"),
        ("La media exigida es de al menos 32 J", "32", "J"),
        ("La valeur doit être au moins 32 J", "32", "J"),
    ],
)
def test_minimum_idioms_are_not_mistaken_for_unary_negative_signs(raw, value, unit):
    claim = normalize(raw, parameter="customer_threshold", value=value, unit=unit, operator=">=")[0]
    assert claim.normalization_state == "parsed" and claim.value == Decimal(value)


def test_exact_scientific_precision_is_unchanged_and_policy_version_is_explicit():
    value = "0.001234567890123456789"
    claim = normalize("Temperature 1.234567890123456789e-3 °C", value=value)[0]
    assert claim.value == Decimal(value) and claim.normalization_state == "parsed"
    assert NORMALIZER_VERSION == "atomic_requirements_v0_38"
