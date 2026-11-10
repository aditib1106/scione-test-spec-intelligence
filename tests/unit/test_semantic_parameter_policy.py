"""A linked translated ID keeps registered numeric rules without editing facts."""

from copy import deepcopy
from decimal import Decimal

import pytest
from test_semantic_matching import claim, proposal, snapshot
from test_semantic_wording import compile_case

from scione.matching.parameter_policy import comparison_pair


@pytest.mark.parametrize("resolution, expected", [("0.05", "supported"), ("0.2", "gap")])
def test_translated_requested_id_keeps_lower_is_better_resolution(resolution, expected):
    requested = claim(
        "req_claim_1",
        parameter_id="resolucion_de_medida",
        label="Resolución",
        operator="<=",
        value=Decimal("0.1"),
        unit="mm",
        raw_text="Resolución <= 0.1 mm.",
    )
    declared = claim(
        "lab_claim_1",
        parameter_id="measurement_resolution",
        operator="=",
        value=Decimal(resolution),
        unit="mm",
        raw_text=f"Resolution {resolution} mm.",
    )
    source = snapshot(requested=[requested], declared=[declared])
    original = deepcopy(source.model_dump(mode="json"))
    run, check = compile_case(source, proposal())
    assert check.status == expected
    assert check.requirement_claim == requested
    assert check.parameter_id == "resolucion_de_medida"
    assert run.input_snapshot.model_dump(mode="json") == original
    assert source.model_dump(mode="json") == original


@pytest.mark.parametrize("nominal, expected", [("140", "supported"), ("212", "gap")])
def test_known_lab_water_policy_preserves_offset_unit_conversion(nominal, expected):
    requested = claim(
        "req_claim_1",
        parameter_id="temperatura_del_agua",
        label="Temperatura del agua",
        operator="=",
        value=Decimal(nominal),
        unit="°F",
        raw_text=f"Agua: {nominal} °F.",
    )
    declared = claim(
        "lab_claim_1",
        parameter_id="water_temperature",
        label="Water temperature",
        operator="range",
        value=None,
        minimum=Decimal("50"),
        maximum=Decimal("70"),
        unit="°C",
        raw_text="Water temperature 50–70 °C.",
    )
    run, check = compile_case(snapshot(requested=[requested], declared=[declared]), proposal())
    assert check.status == expected
    assert check.requirement_claim.value == Decimal(nominal)
    assert check.requirement_claim.unit == "°F"
    assert not run.validation_issues


def test_two_conflicting_known_parameters_are_never_canonicalized_together():
    requested = claim("req_claim_1", parameter_id="water_temperature")
    declared = claim("lab_claim_1", parameter_id="air_temperature")
    first, second = comparison_pair(requested, declared)
    assert first.parameter_id != second.parameter_id
    run, check = compile_case(snapshot(requested=[requested], declared=[declared]), proposal())
    assert check.status == "unknown"
    assert run.validation_issues


def test_link_cannot_repair_an_unresolved_source_fact():
    requested = claim(
        "req_claim_1", parameter_id="temperatura_del_agua", normalization_state="unresolved"
    )
    declared = claim("lab_claim_1", parameter_id="water_temperature")
    _, check = compile_case(snapshot(requested=[requested], declared=[declared]), proposal())
    assert check.status == "unknown"
    assert check.requirement_claim.normalization_state == "unresolved"


def test_material_applicability_and_condition_are_one_operational_restriction():
    requested = claim(
        "req_claim_1",
        parameter_id="specimen_material",
        kind="scope",
        operator="text",
        value=None,
        unit=None,
        text_value="steel",
    )
    declared = requested.model_copy(update={"id": "lab_claim_1", "kind": "condition"})
    run, check = compile_case(snapshot(requested=[requested], declared=[declared]), proposal())
    assert check.status == "supported"
    assert check.kind == "scope"
    assert check.requirement_claim.kind == "scope"
    assert not run.validation_issues


@pytest.mark.parametrize("parameter", ["custom_quality", "specimen_material"])
def test_operational_role_normalization_never_turns_criterion_into_reporting(parameter):
    requested = claim(
        "req_claim_1",
        parameter_id=parameter,
        kind="criterion",
        operator="text",
        value=None,
        unit=None,
        text_value="steel",
    )
    declared = requested.model_copy(update={"id": "lab_claim_1", "kind": "condition"})
    run, check = compile_case(snapshot(requested=[requested], declared=[declared]), proposal())
    assert check.status == "unknown"
    assert run.validation_issues


@pytest.mark.parametrize("operator", ["required", "text"])
def test_a_numeric_report_scale_does_not_establish_qualitative_acceptance(operator):
    requested = claim(
        "req_claim_1",
        parameter_id="custom_quality",
        kind="criterion",
        operator=operator,
        value=None,
        unit=None,
        text_value="must remain blue",
    )
    declared = claim(
        "lab_claim_1",
        parameter_id="custom_quality",
        kind="reporting",
        operator="range",
        value=None,
        minimum=Decimal(0),
        maximum=Decimal(10),
        unit="score",
    )
    _, check = compile_case(snapshot(requested=[requested], declared=[declared]), proposal())
    assert check.status == "unknown"
    assert "qualitative acceptance" in check.reason


def test_explicit_custom_reporting_output_can_still_establish_output_presence():
    requested = claim(
        "req_claim_1",
        parameter_id="custom_output",
        kind="reporting",
        operator="required",
        value=None,
        unit=None,
        text_value="report custom output",
    )
    declared = claim(
        "lab_claim_1",
        parameter_id="custom_output",
        kind="reporting",
        operator="range",
        value=None,
        minimum=Decimal(0),
        maximum=Decimal(10),
        unit="score",
    )
    _, check = compile_case(snapshot(requested=[requested], declared=[declared]), proposal())
    assert check.status == "supported"


@pytest.mark.parametrize("custom_side", ["requested", "declared"])
def test_semantic_id_link_cannot_erase_a_different_known_measurement_label(custom_side):
    requested = claim(
        "req_claim_1",
        parameter_id="measurement_resolution",
        label="Measurement resolution",
        operator="<=",
        value=Decimal("0.1"),
        unit="mm",
        raw_text="Resolution <= 0.1 mm.",
    )
    declared = claim(
        "lab_claim_1",
        parameter_id="measurement_resolution",
        label="Measurement resolution",
        value=Decimal("0.05"),
        unit="mm",
        raw_text="Resolution 0.05 mm.",
    )
    if custom_side == "requested":
        requested = requested.model_copy(
            update={"parameter_id": "surface_error", "label": "Measurement uncertainty"}
        )
    else:
        declared = declared.model_copy(
            update={"parameter_id": "spread_statistic", "label": "Measurement uncertainty"}
        )
    run, check = compile_case(snapshot(requested=[requested], declared=[declared]), proposal())
    assert check.status == "unknown"
    assert "registered label" in run.validation_issues[0].reason
