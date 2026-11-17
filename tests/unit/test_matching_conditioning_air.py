"""An owned conditioning/RH clause can identify an ambient temperature field."""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from scione.matching.comparison import _exact_field_check
from scione.specifications.models import CustomerRequirement
from scione.structured.models import AtomicClaim, NormalizedRequirement


def records():
    requested = AtomicClaim(
        id="req_temperature", parameter_id="temperature", label="Conditioning Temperature",
        kind="condition", stage="conditioning", operator="=", value=23, unit="°C",
        raw_text="Conditioning 7 days at 23 °C, 50 % RH (ISO 3270)", source_pages=[3],
    )
    declared = AtomicClaim(
        id="lab_temperature", parameter_id="air_temperature",
        label="Panel conditioning temperature", kind="condition", stage="conditioning",
        operator="range", minimum=20, maximum=25, unit="°C",
        raw_text="Panel conditioning temperature: 20–25 °C.", normalization_state="reviewed",
    )
    source = CustomerRequirement(
        id="req_001", original_test_name="Gloss", evidence=[dict(page=3, quote=requested.raw_text)],
    )
    return requested, declared, source


def check(requested, declared, source, *, others=(), method_status="supported"):
    return _exact_field_check(
        requested,
        NormalizedRequirement(requirement_id="req_001", test_name="Gloss", claims=[requested]),
        SimpleNamespace(requirement=source),
        {row.id: row for row in [declared, *others]}, method_status,
    )


def test_owned_conditioning_temperature_matches_air_range_without_mutating_source():
    request, lab, source = records()
    original = deepcopy((request.model_dump(), lab.model_dump(), source.model_dump()))
    result = check(request, lab, source)
    assert result is not None and result.status == "supported"
    assert result.requested == "23 °C" and result.declared == "20–25 °C"
    assert result.requirement_claim.parameter_id == "temperature"
    assert result.comparison_basis == "deterministic_exact_fields"
    assert result.comparison_metadata[0]["canonical"] == "air_temperature"
    assert original == (request.model_dump(), lab.model_dump(), source.model_dump())


def test_source_proof_does_not_override_a_range_gap():
    request, lab, source = records()
    lab.maximum = 22
    result = check(request, lab, source)
    assert result is not None and result.status == "gap"


@pytest.mark.parametrize(
    "change",
        ["water", "metal", "immersion", "bath", "fluid", "sample temperature",
         "surface temperature", "no_rh", "wrong_page", "missing_evidence", "curing",
     "different_context", "different_sequence", "different_configuration", "reporting",
     "unresolved", "ambiguous", "method_unknown", "source_value_changed"],
)
def test_conditioning_proof_retains_medium_scope_provenance_and_method_guards(change):
    request, lab, source = records()
    others, method = [], "supported"
    if change in {"water", "metal", "immersion", "bath", "fluid", "sample temperature",
                  "surface temperature"}:
        request.raw_text = request.raw_text.replace("Conditioning", f"Conditioning {change}")
        source.evidence[0].quote = request.raw_text
    elif change == "no_rh":
        request.raw_text = "Conditioning 7 days at 23 °C"
        source.evidence[0].quote = request.raw_text
    elif change == "wrong_page":
        request.source_pages = [4]
    elif change == "missing_evidence":
        source.evidence = []
    elif change == "curing":
        lab.stage = "curing"
    elif change == "different_context":
        lab.source_context = "under nitrogen"
    elif change == "different_sequence":
        lab.sequence = 2
    elif change == "different_configuration":
        request.configuration, lab.configuration = "setup_a", "setup_b"
    elif change == "reporting":
        lab.kind = "reporting"
    elif change == "unresolved":
        request.normalization_state = "unresolved"
    elif change == "ambiguous":
        others = [lab.model_copy(update={"id": "another_lab_temperature"})]
    elif change == "method_unknown":
        method = "unknown"
    elif change == "source_value_changed":
        request.value = Decimal(24)
    assert check(request, lab, source, others=others, method_status=method) is None
