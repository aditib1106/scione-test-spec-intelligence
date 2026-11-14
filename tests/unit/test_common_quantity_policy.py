"""Common quantity policies keep identity, scales and reportability separate."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scione.parameters import (
    REGISTRY_VERSION,
    evaluate_pair,
    normalize_claim,
    resolve_definition,
)
from scione.parameters.units import canonical_unit_name, resolve_unit
from scione.structured.comparison import compare_requirement
from scione.structured.models import AtomicClaim, NormalizedRequirement


def claim(parameter, **updates):
    return AtomicClaim.model_validate(
        {
            "id": "source_fact",
            "parameter_id": parameter,
            "label": parameter,
            "kind": "criterion",
            "stage": "general",
            "operator": ">=",
            "value": "42",
            "unit": "J",
            "raw_text": "Explicit typed source statement",
            **updates,
        }
    )


def reporting(parameter, low="0", high="100", **updates):
    values = dict(
        id="lab_output",
        kind="reporting",
        operator="range",
        value=None,
        minimum=low,
        maximum=high,
    )
    values.update(updates)
    return claim(parameter, **values)


def status(request, declaration):
    result = evaluate_pair(request, declaration)
    assert result is not None
    return result.status


def custom_status(request, declaration):
    result = compare_requirement(
        NormalizedRequirement(requirement_id="req_policy", test_name="Example", claims=[request]),
        [declaration],
    )
    return next(check for check in result.checks if check.id == request.id)


def test_registry_version_and_narrow_aliases_do_not_erase_statistics():
    assert REGISTRY_VERSION == "pilot_parameters_v7"
    assert resolve_definition("ultimate tensile strength").id == "tensile_strength"
    assert resolve_definition("抗压强度").id == "compressive_strength"
    assert resolve_definition("elongation at fracture").id == "elongation_at_break"
    assert resolve_definition("Ritzunterwanderung").id == "coating_creep"
    for ambiguous in ["strength", "hardness", "mean", "impact_energy_mean", "gloss_range"]:
        assert resolve_definition(ambiguous) is None
    original = claim("custom_energy_mean", label="Absorbed impact energy")
    assert normalize_claim(original).parameter_id == "custom_energy_mean"


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("cold_step_temperature", "temperature"),
        ("hot_step_temperature", "temperature"),
        ("cure_metal_temperature", "temperature"),
        ("conditioning_temperature", "temperature"),
        ("cold_step_duration", "duration"),
        ("hot_step_duration", "duration"),
        ("cure_duration", "duration"),
        ("conditioning_duration", "duration"),
        ("recovery_duration", "duration"),
    ],
)
def test_context_prefixed_quantity_names_keep_their_separate_scope(alias, canonical):
    request = claim(
        alias,
        kind="condition",
        stage="curing",
        sequence=2,
        source_context="on steel",
        unit="°C" if canonical == "temperature" else "min",
    )
    normalized = normalize_claim(request)
    assert normalized.parameter_id == canonical
    assert normalized.stage == "curing"
    assert normalized.sequence == 2
    assert normalized.source_context == "on steel"
    same = normalized.model_copy(update={"id": "lab"})
    assert status(request, same.model_copy(update={"stage": "conditioning"})) == "unknown"
    assert status(request, same.model_copy(update={"sequence": 1})) == "unknown"
    assert status(request, same.model_copy(update={"source_context": "on aluminium"})) == "unknown"
    # Quantity aliases do not invent the stage when the source failed to record it.
    assert normalize_claim(request.model_copy(update={"stage": "general"})).stage == "general"


def test_strength_reportability_converts_mpa_gpa_without_predicting_success():
    request = claim(
        "tensile_strength", operator="range", value=None, minimum="860", maximum="1020", unit="MPa"
    )
    lab = reporting("tensile_strength", low="0.5", high="1.1", unit="GPa")
    result = evaluate_pair(request, lab)
    assert result.status == "supported"
    assert "specimen pass/fail is not predicted" in result.reason
    assert status(request, reporting("tensile_strength", high="0.9", unit="GPa")) == "gap"
    assert request.minimum == Decimal(860)
    assert lab.maximum == Decimal("1.1")


@pytest.mark.parametrize(
    "requested,lab",
    [
        (
            claim("tensile_strength", value="38", unit="MPa"),
            reporting("compressive_strength", unit="MPa"),
        ),
        (claim("tensile_strength", value="38", unit="MPa"), reporting("pressure", unit="MPa")),
        (
            claim("tensile_force", value="820", unit="N"),
            reporting("tensile_strength", high="1000", unit="MPa"),
        ),
        (claim("tensile_force", value="820", unit="N"), reporting("load", high="1000", unit="g")),
        (claim("tensile_force", value="820", unit="N"), reporting("force", high="1000", unit="N")),
    ],
)
def test_same_number_or_unit_cannot_bridge_distinct_physical_properties(requested, lab):
    assert status(requested, lab) == "unknown"


def test_force_reporting_and_energy_reporting_use_exact_si_units():
    assert (
        status(
            claim("tensile_force", value="820", unit="N"),
            reporting("tensile_force", low="0.5", high="1.0", unit="kN"),
        )
        == "supported"
    )
    assert (
        status(claim("impact_energy"), reporting("impact_energy", high="0.05", unit="kJ"))
        == "supported"
    )
    assert (
        status(
            claim("impact_energy", value="60"), reporting("impact_energy", high="0.05", unit="kJ")
        )
        == "gap"
    )
    assert (
        status(claim("impact_energy", unit="kJ/m²"), reporting("impact_energy", unit="J"))
        == "unknown"
    )


def test_elongation_is_a_reported_fracture_fraction_not_prescribed_compression():
    request = claim("elongation_at_break", value="11", unit="%")
    assert status(request, reporting("elongation_at_fracture", unit="%")) == "supported"
    assert status(request, reporting("detached_grid_area", unit="%")) == "unknown"
    assert resolve_definition("compression_strain") is None


def test_spread_and_detachment_do_not_borrow_absolute_or_ordinal_scales():
    spread = claim("gloss_spread", operator="<=", value="8", unit="GU")
    assert status(spread, reporting("gloss_spread", unit="GU")) == "supported"
    assert status(spread, reporting("gloss", high="200", unit="GU")) == "unknown"
    area = claim("detached_grid_area", operator="<=", value="5", unit="%")
    assert status(area, reporting("flaked_grid_area_percentage", unit="%")) == "supported"
    assert status(area, reporting("adhesion_class", high="5", unit="class")) == "unknown"
    assert status(area, reporting("gloss_retention", unit="%")) == "unknown"
    assert (
        status(
            claim("adhesion_class", value="5", unit="%"),
            reporting("adhesion_class", high="5", unit="class"),
        )
        == "unknown"
    )


def test_creep_output_retains_context_and_mean_maximum_scopes_are_not_aliases():
    request = claim(
        "coating_creep", operator="<=", value="1.5", unit="mm", configuration="panel_mean"
    )
    assert (
        status(
            request, reporting("scribe_creep", high="0.01", unit="m", configuration="panel_mean")
        )
        == "supported"
    )
    assert (
        status(request, reporting("scribe_creep", unit="mm", configuration="maximum_station"))
        == "unknown"
    )
    assert resolve_definition("scribe_creep_mean") is None
    assert resolve_definition("creep") is None  # Bulk material creep is not coating undercut.


def test_hardness_scales_are_named_and_never_converted_between_methods():
    assert (
        status(
            claim("rockwell_hardness", value="34", unit="HRC"),
            reporting("rockwell_c_hardness", high="70", unit="HRC"),
        )
        == "supported"
    )
    assert (
        status(
            claim("vickers_hardness", value="345", unit="HV"),
            reporting("vickers_microhardness", high="1000", unit="HV"),
        )
        == "supported"
    )
    assert (
        status(
            claim("vickers_hardness", value="345", unit="HV"),
            reporting("rockwell_hardness", high="70", unit="HRC"),
        )
        == "unknown"
    )
    assert (
        status(
            claim("vickers_hardness", value="345", unit="HV"),
            reporting("vickers_hardness", high="1000", unit="HRC"),
        )
        == "unknown"
    )
    assert (
        status(
            claim("vickers_hardness", value="345", unit="HV"),
            reporting("vickers_hardness", high="10", unit="GPa"),
        )
        == "unknown"
    )
    assert (
        status(
            claim("rockwell_hardness", value="34", unit="HRB"),
            reporting("rockwell_hardness", high="100", unit="HRC"),
        )
        == "unknown"
    )


def test_panel_specimen_cycle_and_location_counts_are_distinct():
    panel = claim("panel_count", kind="condition", operator="=", value="3", unit="panels")
    assert status(panel, reporting("panel_count", kind="condition", unit="试板")) == "supported"
    assert (
        status(panel, reporting("specimen_count", kind="condition", unit="specimens")) == "unknown"
    )
    assert (
        status(
            claim("cycles", kind="condition", operator="=", value="3", unit="块试板"),
            reporting("cycles", kind="condition", unit="cycles"),
        )
        == "unknown"
    )
    assert (
        status(
            claim(
                "measurement_locations", kind="condition", operator="=", value="3", unit="panels"
            ),
            reporting("measurement_locations", kind="condition", unit="locations"),
        )
        == "unknown"
    )
    assert (
        status(
            claim("specimen_count", kind="condition", operator="=", value="3", unit=None),
            reporting("specimen_count", kind="condition", unit="specimens"),
        )
        == "supported"
    )


def test_custom_quantities_get_only_known_zero_offset_same_dimension_conversions():
    assert (
        custom_status(
            claim("custom_energy"), reporting("custom_energy", high="0.05", unit="kJ")
        ).status
        == "supported"
    )
    assert (
        custom_status(
            claim("custom_energy", value="60"), reporting("custom_energy", high="0.05", unit="kJ")
        ).status
        == "gap"
    )
    assert (
        custom_status(
            claim("custom_quantity", unit="N"), reporting("custom_quantity", unit="g")
        ).status
        == "unknown"
    )
    assert (
        custom_status(
            claim("custom_temperature", value="60", unit="°C"),
            reporting("custom_temperature", low="140", high="140", unit="°F"),
        ).status
        == "unknown"
    )
    assert (
        status(
            claim("temperature", kind="condition", operator="=", value="60", unit="°C"),
            reporting("temperature", kind="condition", low="140", high="140", unit="°F"),
        )
        == "supported"
    )
    # Unknown literal scales retain the existing explicit same-unit behavior.
    assert (
        custom_status(
            claim("custom_score", value="42", unit="Q7-score"),
            reporting("custom_score", unit="Q7-score"),
        ).status
        == "supported"
    )


def test_unit_names_preserve_si_prefix_case_and_distinct_hardness_dimensions():
    assert canonical_unit_name("joules") == "J"
    assert canonical_unit_name("Vickers HV") == "HV"
    assert resolve_unit("GPa").factor == 1_000_000_000
    assert resolve_unit("Gpa") is None
    assert resolve_unit("HRC").dimension != resolve_unit("HV").dimension
    assert resolve_unit("panels").dimension != resolve_unit("cycles").dimension
