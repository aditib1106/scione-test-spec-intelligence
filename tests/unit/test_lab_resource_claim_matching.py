"""Resource shortcuts retain the existing conservative domain comparison rules."""

from copy import deepcopy

import pytest

from scione.parameters import evaluate_pair, resolve_definition
from scione.structured.comparison import compare_requirement
from scione.structured.models import AtomicClaim, NormalizedRequirement


def resource(identifier, text, **changes):
    return AtomicClaim(
        id=identifier, parameter_id="equipment", label="Equipment categories",
        kind="condition", stage="general", operator="text", text_value=text,
        raw_text=f"Equipment categories: {text}.", normalization_state="reviewed",
        **changes,
    )


def compare(request, declarations, *, method_status="supported"):
    normalized = NormalizedRequirement(
        requirement_id="req_resource", test_name="A declared test", claims=[request],
    )
    result = compare_requirement(normalized, declarations, method_status=method_status)
    return result, next(check for check in result.checks if check.id == request.id)


def test_equipment_categories_use_existing_required_set_subset_policy_without_mutation():
    assert resolve_definition("equipment").rule == "required_set"
    request = resource("requested", "temperature controlled chamber; uv lamp")
    declaration = resource(
        "declared", "spectrophotometer\nuv lamp\ntemperature controlled chamber"
    )
    before = deepcopy((request.model_dump(), declaration.model_dump()))
    evaluation = evaluate_pair(request, declaration)
    assert evaluation is not None and evaluation.status == "supported"
    result, check = compare(request, [declaration])
    assert result.status == "fully_matched"
    assert check.status == "supported"
    assert check.capability_claim_ids == [declaration.id]
    assert (request.model_dump(), declaration.model_dump()) == before


def test_an_unrecorded_equipment_category_is_unknown_not_proven_unavailable():
    request = resource("requested", "temperature controlled chamber; uv lamp")
    declaration = resource("declared", "temperature controlled chamber")
    evaluation = evaluate_pair(request, declaration)
    assert evaluation is not None and evaluation.status == "unknown"
    result, check = compare(request, [declaration])
    assert result.status == "needs_information"
    assert check.status == "unknown"


def test_a_known_multilingual_category_uses_only_existing_registered_equivalence():
    request = resource("requested", "分光光度计; 紫外灯")
    declaration = resource("declared", "spectrophotometer; uv lamp")
    result, check = compare(request, [declaration])
    assert result.status == "fully_matched" and check.status == "supported"


def test_unknown_or_incompatible_resource_context_cannot_establish_support():
    request = resource("requested", "uv lamp")
    unavailable = resource("declared", "uv lamp", availability="unknown")
    _, check = compare(request, [unavailable])
    assert check.status == "unknown"
    incompatible = resource("declared", "uv lamp", configuration="wet-exposure-only")
    scoped_request = request.model_copy(update={"configuration": "dry-exposure-only"})
    _, check = compare(scoped_request, [incompatible])
    assert check.status == "unknown"


def test_present_equipment_never_establishes_a_missing_test_method():
    request = resource("requested", "uv lamp")
    declaration = resource("declared", "uv lamp")
    result, check = compare(request, [declaration], method_status="gap")
    assert check.status == "supported"
    assert result.checks[0].status == "gap"
    assert result.status == "not_matched"


@pytest.mark.parametrize("requested_parameter", ["specimen_material", "equipment"])
def test_custom_supplies_text_cannot_stand_in_for_specimen_or_equipment(requested_parameter):
    text = "steel panel" if requested_parameter == "specimen_material" else "uv lamp"
    request = resource("requested", text).model_copy(update={
        "parameter_id": requested_parameter,
        "label": "Specimen material" if requested_parameter == "specimen_material"
        else "Equipment categories",
    })
    supplies = AtomicClaim(
        id="cap_resource_supplies_test", parameter_id="custom_lab_supplies",
        label="Consumables & materials", kind="condition", stage="general",
        operator="text", text_value=text, raw_text=f"Consumables & materials: {text}.",
        normalization_state="unresolved",
    )
    before = supplies.model_dump()
    result, check = compare(request, [supplies])
    assert check.status == "unknown"
    assert check.capability_claim_ids == []
    assert result.status == "needs_information"
    assert supplies.model_dump() == before


def test_supplies_note_is_not_promoted_by_even_identical_custom_requested_wording():
    supplies = AtomicClaim(
        id="lab_supplies", parameter_id="custom_lab_supplies",
        label="Consumables & materials", kind="condition", operator="text",
        text_value="Adhesion tape", raw_text="Adhesion tape",
        normalization_state="unresolved",
    )
    request = supplies.model_copy(update={"id": "requested_supplies"})
    result, check = compare(request, [supplies])
    assert check.status == "unknown"
    assert result.status == "needs_information"
