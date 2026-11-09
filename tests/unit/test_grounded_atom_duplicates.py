"""Typed facts suppress parser copies, never unseen source restrictions."""

from decimal import Decimal

import pytest

from scione.specifications.models import CustomerRequirement
from scione.structured import AtomicClaim, normalize_requirement


def atom(identifier, parameter, raw, *, value=None, unit=None, stage="general", **fields):
    return AtomicClaim(
        id=identifier,
        parameter_id=parameter,
        label=parameter,
        raw_text=raw,
        source_pages=[1],
        stage=stage,
        operator="=" if value is not None else "text",
        value=Decimal(str(value)) if value is not None else None,
        unit=unit,
        **fields,
    )


def requirement(raw, atoms, *, kind="condition", context=None):
    return CustomerRequirement(
        id="req_one",
        original_test_name="Example test",
        conditions=[{"name": "Conditions", "raw_value": raw, "context": context}]
        if kind == "condition"
        else [],
        acceptance_criteria=[{"name": "Criteria", "raw_value": raw}] if kind == "criterion" else [],
        evidence=[{"page": 1, "quote": raw}],
        atomic_claims=atoms,
    )


def test_conditioning_labels_and_curated_aliases_are_not_extra_requirements():
    raw = "Conditioning stage: temprature 23 °C for 6 h（调节阶段）."
    atoms = [
        atom("t", "temperature", "temprature 23 °C", value=23, unit="°C", stage="conditioning"),
        atom("d", "duration", "for 6 h", value=6, unit="h", stage="conditioning"),
    ]
    claims = normalize_requirement(requirement(raw, atoms)).claims
    assert len(claims) == 2
    assert all(claim.normalization_state == "parsed" for claim in claims)


def test_measurement_and_acceptance_headers_do_not_create_residual_claims():
    angle = "Measurement stage: observation angle 60°."
    claims = normalize_requirement(
        requirement(
            angle,
            [
                atom(
                    "a",
                    "measurement_angle",
                    "observation angle 60°",
                    value=60,
                    unit="°",
                    stage="measurement",
                )
            ],
        )
    ).claims
    assert len(claims) == 1
    assert claims[0].stage == "measurement"
    criterion = "After-cycle acceptance criterion: adhesion class <=1."
    claim = atom(
        "c", "adhesion_class", "adhesion class <=1", value=1, stage="after_cycle", kind="criterion"
    )
    claim.operator = "<="
    claims = normalize_requirement(requirement(criterion, [claim], kind="criterion")).claims
    assert len(claims) == 1
    assert claims[0].operator == "<="
    assert claims[0].stage == "after_cycle"


def test_enclosing_cycle_header_preserves_step_two_without_generic_duplicates():
    raw = (
        "Execute these thermal-cycle stages in order: step 1 at -12 °C for 45 min; "
        "step 2 at 65 °C for 75 min."
    )
    atoms = []
    for step, temperature, duration in ((1, -12, 45), (2, 65, 75)):
        fragment = f"step {step} at {temperature} °C for {duration} min"
        for parameter, number, unit in (
            ("temperature", temperature, "°C"),
            ("duration", duration, "min"),
        ):
            atoms.append(
                atom(
                    f"{parameter}_{step}",
                    parameter,
                    fragment,
                    value=number,
                    unit=unit,
                    stage="thermal_cycle",
                    sequence=step,
                )
            )
    claims = normalize_requirement(requirement(raw, atoms)).claims
    assert len(claims) == 4
    assert {(claim.parameter_id, claim.sequence) for claim in claims} == {
        ("temperature", 1),
        ("duration", 1),
        ("temperature", 2),
        ("duration", 2),
    }


def test_curated_resource_presentation_does_not_duplicate_master_panel():
    raw = "compare with a master panel."
    claim = atom(
        "panel",
        "reference_panel",
        "compare with a master panel",
        stage="measurement",
        text_value="master panel",
    )
    claim.operator = "required"
    claims = normalize_requirement(requirement(raw, [claim])).claims
    assert len(claims) == 1
    assert claims[0].text_value == "master panel"


@pytest.mark.parametrize(
    "qualifier", ["under nitrogen", "except nitrogen", "only on aluminium", "not on steel"]
)
def test_covered_quantities_do_not_erase_unrepresented_restrictions(qualifier):
    raw = f"Conditioning stage: 23 °C {qualifier}."
    claim = atom("t", "temperature", raw, value=23, unit="°C", stage="conditioning")
    claims = normalize_requirement(requirement(raw, [claim])).claims
    unresolved = [item for item in claims if item.normalization_state == "unresolved"]
    assert unresolved
    assert any(
        word in (unresolved[0].text_value or "") for word in ("nitrogen", "aluminium", "steel")
    )


def test_one_grounded_quantity_does_not_hide_a_missing_second_quantity():
    raw = "Conditioning stage: 23 °C for 12 h."
    claim = atom("t", "temperature", "23 °C", value=23, unit="°C", stage="conditioning")
    claims = normalize_requirement(requirement(raw, [claim])).claims
    assert any(item.parameter_id == "duration" and item.value == Decimal(12) for item in claims)


def test_wrong_value_and_different_source_material_keep_fallbacks():
    raw = "Conditioning stage: 23 °C on steel."
    claim = atom(
        "t",
        "temperature",
        raw,
        value=99,
        unit="°C",
        stage="conditioning",
        source_context="aluminium",
    )
    claims = normalize_requirement(requirement(raw, [claim])).claims
    assert claims[0].normalization_state == "unresolved"
    assert any(item.parameter_id == "temperature" and item.value == 23 for item in claims)
    assert any("steel" in (item.text_value or "") for item in claims)


def test_step_header_does_not_override_a_named_conflicting_stage():
    raw = "Conditioning stage: step 2 at 23 °C."
    claim = atom(
        "t",
        "temperature",
        "step 2 at 23 °C",
        value=23,
        unit="°C",
        stage="thermal_cycle",
        sequence=2,
    )
    claims = normalize_requirement(requirement(raw, [claim])).claims
    assert any(
        item.parameter_id == "temperature" and item.stage == "conditioning" for item in claims
    )


def test_duration_short_quote_inherits_only_its_unique_source_step():
    raw = "Execute thermal-cycle stages: step 1 at -12 °C for 45 min; step 2 at 65 °C for 75 min."
    atoms = [
        atom(
            "t1",
            "temperature",
            "step 1 at -12 °C",
            value=-12,
            unit="°C",
            stage="thermal_cycle",
            sequence=1,
        ),
        atom(
            "d1",
            "duration",
            "-12 °C for 45 min",
            value=45,
            unit="min",
            stage="thermal_cycle",
            sequence=1,
        ),
        atom(
            "t2",
            "temperature",
            "step 2 at 65 °C",
            value=65,
            unit="°C",
            stage="thermal_cycle",
            sequence=2,
        ),
        atom(
            "d2",
            "duration",
            "65 °C for 75 min",
            value=75,
            unit="min",
            stage="thermal_cycle",
            sequence=2,
        ),
    ]
    claims = normalize_requirement(requirement(raw, atoms)).claims
    assert len(claims) == 4
    assert all(item.stage == "thermal_cycle" for item in claims)


def test_short_quote_cannot_inherit_a_different_step_than_its_source_position():
    raw = "Thermal-cycle stages: step 1 at 23 °C; step 2 at 24 °C for 75 min."
    wrong = atom(
        "d", "duration", "24 °C for 75 min", value=75, unit="min", stage="thermal_cycle", sequence=1
    )
    claims = normalize_requirement(requirement(raw, [wrong])).claims
    assert any(item.parameter_id == "duration" and item.sequence is None for item in claims)


def test_repeated_short_quote_cannot_hide_a_second_source_step():
    raw = "Thermal-cycle stages: step 1 at 23 °C for 45 min then step 2 at 23 °C for 45 min."
    claim = atom(
        "d", "duration", "23 °C for 45 min", value=45, unit="min", stage="thermal_cycle", sequence=1
    )
    claims = normalize_requirement(requirement(raw, [claim])).claims
    durations = [item for item in claims if item.parameter_id == "duration"]
    assert len(durations) == 2
    assert any(item.sequence == 2 and "_field_" in item.id for item in durations)


@pytest.mark.parametrize("qualification", ["under nitrogen", "except steel", "and roughness <=3"])
def test_ordinal_criterion_deduplication_preserves_uncovered_restrictions(qualification):
    raw = f"After-cycle acceptance criterion: adhesion class <=1 {qualification}."
    claim = atom(
        "c", "adhesion_class", "adhesion class <=1", value=1, stage="after_cycle", kind="criterion"
    )
    claim.operator = "<="
    claims = normalize_requirement(requirement(raw, [claim], kind="criterion")).claims
    assert any(item.normalization_state == "unresolved" for item in claims)
    assert any(
        word in (item.text_value or "")
        for item in claims
        for word in ("nitrogen", "steel", "roughness")
    )


def test_ordinal_criterion_does_not_hide_a_changed_bound():
    raw = "After-cycle acceptance criterion: adhesion class <=1."
    claim = atom("c", "adhesion_class", raw, value=1, stage="after_cycle", kind="criterion")
    claim.operator = ">="
    claims = normalize_requirement(requirement(raw, [claim], kind="criterion")).claims
    assert any(item.parameter_id == "adhesion_class" and item.operator == "<=" for item in claims)
