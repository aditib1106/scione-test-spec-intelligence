"""Quantity identity includes its local step and represented restrictions."""

from decimal import Decimal

import pytest

from scione.specifications.models import CustomerRequirement
from scione.structured import AtomicClaim, compare_requirement, normalize_requirement


def source(raw, claims, *, headers=(), page=1, kind="condition"):
    return CustomerRequirement(
        id="req_unrelated",
        original_test_name="Independent example",
        conditions=[{"name": "Conditions", "raw_value": raw}] if kind == "condition" else [],
        acceptance_criteria=[{"name": "Acceptance", "raw_value": raw}]
        if kind == "criterion"
        else [],
        evidence=[{"page": page, "quote": raw}]
        + [{"page": p, "quote": text} for p, text in headers],
        atomic_claims=claims,
    )


def quantity(raw, *, parameter="duration", value="37", unit="min", **fields):
    return AtomicClaim(
        id="model_quantity",
        parameter_id=parameter,
        label=parameter,
        raw_text=raw,
        source_pages=[1],
        operator="=",
        value=Decimal(value),
        unit=unit,
        **fields,
    )


def test_explicit_local_exposure_header_and_preserved_environment_replace_parser_copies():
    raw = "Duración de exposición: 37 min, dentro de la cámara de presión."
    claim = quantity(raw, stage="exposure", source_context="dentro de la cámara de presión")
    requirement = source(raw, [claim], headers=[(1, "Stage: exposure.\n" + raw)])
    normalized = normalize_requirement(requirement)
    assert len(normalized.claims) == 1
    assert normalized.claims[0].model_dump(exclude={"id"}) == claim.model_dump(exclude={"id"})
    assert requirement.atomic_claims[0].source_context == "dentro de la cámara de presión"


@pytest.mark.parametrize(
    "headers",
    [(), ((2, "Stage: exposure."),), ((1, "Stage: exposure."), (1, "Stage: curing."))],
)
def test_absent_wrong_page_or_conflicting_stage_header_cannot_hide_fallback(headers):
    raw = "Duration: 37 min."
    normalized = normalize_requirement(
        source(raw, [quantity(raw, stage="exposure")], headers=headers)
    )
    numeric = [claim for claim in normalized.claims if claim.value is not None]
    assert len(numeric) == 1 and numeric[0].stage == "exposure"
    assert normalized.review_issues
    compared = compare_requirement(normalized, [numeric[0].model_copy(deep=True)])
    assert compared.status == "needs_information"
    assert any(check.kind == "scope" and check.status == "unknown" for check in compared.checks)


def test_configuration_duplicate_removal_preserves_source_configuration_on_primary_atom():
    raw = "Setup B: duration 37 min."
    claim = quantity(raw, configuration="Setup B")
    normalized = normalize_requirement(source(raw, [claim]))
    numeric = [item for item in normalized.claims if item.value is not None]
    assert len(numeric) == 1
    assert numeric[0].configuration == "Setup B"


def test_unstated_configuration_cannot_hide_fallback():
    raw = "Duration: 37 min."
    normalized = normalize_requirement(source(raw, [quantity(raw, configuration="Setup B")]))
    numeric = [claim for claim in normalized.claims if claim.value is not None]
    assert len(numeric) == 1 and numeric[0].configuration == "Setup B"
    assert normalized.review_issues
    assert compare_requirement(normalized, [numeric[0].model_copy(deep=True)]).status == (
        "needs_information"
    )


@pytest.mark.parametrize("extra", ["not on copper", "except steel", "only under argon"])
def test_preserved_context_never_consumes_an_additional_material_or_negative_restriction(extra):
    raw = f"Exposure duration: 37 min in humidity chamber, {extra}."
    claim = quantity(raw, stage="exposure", source_context="in humidity chamber")
    normalized = normalize_requirement(
        source(raw, [claim], headers=[(1, "Stage: exposure.\n" + raw)])
    )
    unresolved = [item for item in normalized.claims if item.normalization_state == "unresolved"]
    assert unresolved
    assert any(extra.split()[-1] in (item.text_value or "") for item in unresolved)


def test_cjk_label_and_copula_are_syntax_but_an_unknown_qualification_survives():
    raw = "表面光泽度为 72 GU。"
    claim = quantity(raw, parameter="gloss", value="72", unit="GU", kind="criterion")
    assert len(normalize_requirement(source(raw, [claim], kind="criterion")).claims) == 1
    restricted = "表面光泽度为 72 GU，仅限铝板。"
    claim = claim.model_copy(update={"raw_text": restricted})
    normalized = normalize_requirement(source(restricted, [claim], kind="criterion"))
    assert any("仅限铝板" in (item.text_value or "") for item in normalized.claims)


def test_acceptance_label_is_not_a_second_unknown_criterion():
    raw = "Acceptance: colour difference <= 0.35 ΔE*ab."
    claim = quantity(
        raw, parameter="color_difference", value="0.35", unit="ΔE*ab", kind="criterion"
    ).model_copy(update={"operator": "<="})
    normalized = normalize_requirement(source(raw, [claim], kind="criterion"))
    assert len(normalized.claims) == 1
    assert normalized.claims[0].operator == "<="


@pytest.mark.parametrize("number,step,parsed", [("10", 1, True), ("70", 1, False), ("70", 2, True)])
def test_full_multi_step_quote_does_not_justify_wrong_temperature_step(number, step, parsed):
    raw = "Thermal-cycle step 1 at 10 °C; step 2 at 70 °C."
    claim = quantity(
        raw, parameter="temperature", value=number, unit="°C", stage="thermal_cycle", sequence=step
    )
    normalized = normalize_requirement(source(raw, [claim]))
    assert (normalized.claims[0].normalization_state == "parsed") is parsed
    if not parsed:
        assert normalized.claims[0].availability == "unknown"
        assert any(item.value == 10 and item.sequence == 1 for item in normalized.claims)
    assert claim.normalization_state == "parsed"  # Input artifact remains immutable.


def test_multi_step_number_must_belong_to_the_stated_unit_inside_that_step():
    raw = "Step 1 at 10 °C for 70 min; step 2 at 70 °C for 10 min."
    claim = quantity(
        raw, parameter="temperature", value="70", unit="°C", stage="thermal_cycle", sequence=1
    )
    normalized = normalize_requirement(source(raw, [claim]))
    assert normalized.claims[0].normalization_state == "unresolved"


def test_global_cycle_count_is_not_associated_with_one_temperature_step():
    raw = "Perform 8 cycles: step 1 at 10 °C; step 2 at 70 °C."
    claim = quantity(raw, parameter="cycles", value="8", unit="cycles", stage="thermal_cycle")
    normalized = normalize_requirement(source(raw, [claim]))
    assert normalized.claims[0].value == 8
    assert normalized.claims[0].normalization_state == "parsed"
    assert normalized.claims[0].sequence is None


@pytest.mark.parametrize("atomic_kind,raw_kind", [("condition", "scope"), ("scope", "condition")])
def test_known_operational_category_can_deduplicate_a_different_raw_field_role(
    atomic_kind, raw_kind
):
    raw = "Required specimen material: aluminium."
    material = AtomicClaim(
        id="material",
        parameter_id="specimen_material",
        label="Specimen material",
        kind=atomic_kind,
        operator="text",
        text_value="aluminium",
        raw_text=raw,
        source_pages=[1],
    )
    requirement = source(raw, [material], kind=raw_kind)
    if raw_kind == "scope":
        requirement.applicability = [raw]
    normalized = normalize_requirement(requirement)
    assert len(normalized.claims) == 1
    assert normalized.claims[0].kind == atomic_kind  # Historical role is preserved.


@pytest.mark.parametrize(
    "parameter,extra", [("specimen_material", "except copper"), ("unfamiliar_resource", "")]
)
def test_cross_role_quote_alone_never_erases_unrepresented_qualification_or_unknown_parameter(
    parameter, extra
):
    raw = f"Required specimen material: aluminium {extra}."
    material = AtomicClaim(
        id="material",
        parameter_id=parameter,
        label="Specimen material",
        kind="condition",
        operator="text",
        text_value="aluminium",
        raw_text=raw,
        source_pages=[1],
    )
    requirement = source(raw, [material], kind="scope")
    requirement.applicability = [raw]
    normalized = normalize_requirement(requirement)
    assert any(item.kind == "scope" for item in normalized.claims)



