"""Humidity unit aliases cannot turn shared-page facts into false omissions."""

from decimal import Decimal

import pytest

from scione.specifications.models import (
    CustomerRequirement,
    RequestedMethod,
    RequirementEvidence,
    RequirementValue,
)
from scione.structured import normalize_requirement
from scione.structured.models import AtomicClaim

SOURCE = "Conditioning    7 days at 23 °C, 50 % RH."


def source_requirement(*, parameter="relative_humidity", value="50", unit="%", include=True):
    atoms = [
        AtomicClaim(
            id="duration", parameter_id="duration", label="Duration", operator="=",
            value=7, unit="days", stage="conditioning", raw_text=SOURCE, source_pages=[1],
        ),
        AtomicClaim(
            id="temperature", parameter_id="temperature", label="Temperature", operator="=",
            value=23, unit="°C", stage="conditioning", raw_text=SOURCE, source_pages=[1],
        ),
    ]
    if include:
        atoms.append(AtomicClaim(
            id="humidity", parameter_id=parameter, label="Declared property", operator="=",
            value=Decimal(value), unit=unit, stage="conditioning",
            raw_text=SOURCE, source_pages=[1],
        ))
    return CustomerRequirement(
        id="req_humidity", original_test_name="Gloss measurement",
        requested_methods=[RequestedMethod(code="ISO 2813")],
        conditions=[RequirementValue(
            name="Conditioning", raw_value="Conditioning: 7 days at 23 °C, 50 % RH.",
        )],
        evidence=[
            RequirementEvidence(page=1, quote=SOURCE),
            RequirementEvidence(page=2, quote="Gloss measurement per ISO 2813."),
        ],
        atomic_claims=atoms,
    )


@pytest.mark.parametrize("unit", ["%", "% RH"])
def test_shared_source_humidity_percent_alias_has_no_missing_quantity_warning(unit):
    source = source_requirement(unit=unit)
    before = source.model_dump_json()
    normalized = normalize_requirement(source, native_first=True)
    assert not any("Unrepresented source quantity: 50 % RH." in s
                   for s in normalized.review_issues)
    humidity = next(c for c in normalized.claims if c.parameter_id == "relative_humidity")
    assert humidity.value == Decimal("50") and humidity.unit == unit
    assert source.model_dump_json() == before


@pytest.mark.parametrize("changes", [
    {"parameter": "gloss_retention"},
    {"parameter": "mass_fraction"},
    {"value": "45"},
    {"include": False},
])
def test_other_percent_quantities_wrong_values_and_missing_humidity_still_require_review(changes):
    normalized = normalize_requirement(source_requirement(**changes), native_first=True)
    assert any("Unrepresented source quantity: 50 % RH." in s
               for s in normalized.review_issues)
