"""Semantic links must not invent resource identity or broaden negative evidence."""

from copy import deepcopy
from decimal import Decimal

import pytest

from scione.parameters import evaluate_pair
from scione.structured.comparison import compare_requirement
from scione.structured.models import AtomicClaim, NormalizedRequirement


def categorical(parameter, value, *, identifier="fact", availability="supported", **updates):
    fields = {
        "id": identifier,
        "parameter_id": parameter,
        "label": parameter,
        "kind": "condition",
        "stage": "measurement",
        "operator": "required",
        "text_value": value,
        "availability": availability,
        "raw_text": f"Declared {parameter}: {value or 'identity not recorded'}.",
    }
    fields.update(updates)
    return AtomicClaim(**fields)


def numeric(parameter="temperature", *, identifier="fact", availability="supported", **updates):
    fields = {
        "id": identifier,
        "parameter_id": parameter,
        "label": parameter,
        "kind": "condition",
        "stage": "measurement",
        "operator": "=",
        "value": Decimal("60"),
        "unit": "°C",
        "availability": availability,
        "raw_text": "Source-declared temperature 60 °C.",
    }
    fields.update(updates)
    return AtomicClaim(**fields)


def cases():
    """Manual expectations stated before the implementation change, not predictions."""
    return [
        (
            "custom_required_missing_identity",
            categorical("custom_probe", None, raw_text="Probe QX-9 is required."),
            categorical("custom_probe", "QX-7"),
            "unknown",
            "The typed source lacks its resource identity; another probe cannot establish support.",
        ),
        (
            "custom_required_exact_identity",
            categorical("custom_probe", "QX-9"),
            categorical("custom_probe", "QX-9"),
            "supported",
            "An exact declared resource identity remains usable without inventing an alias.",
        ),
        (
            "custom_required_case_not_established",
            categorical("custom_probe", "XQ-a"),
            categorical("custom_probe", "XQ-A"),
            "unknown",
            "Case equivalence is not established for an unfamiliar identifier.",
        ),
        (
            "custom_required_punctuation_not_equivalent",
            categorical("custom_probe", "XQ-7"),
            categorical("custom_probe", "XQ+7"),
            "unknown",
            "A distinct punctuation-bearing identifier is not the same declared resource.",
        ),
        (
            "custom_text_case_not_established",
            categorical("custom_protocol", "XQ-a", operator="text"),
            categorical("custom_protocol", "XQ-A", operator="text"),
            "unknown",
            "The fallback must not erase case in a novel protocol identifier.",
        ),
        (
            "custom_text_exact_identity",
            categorical("custom_protocol", "XQ-a", operator="text"),
            categorical("custom_protocol", "XQ-a", operator="text"),
            "supported",
            "Literal custom text remains comparable when its identity is complete.",
        ),
        (
            "curated_equipment_translation",
            categorical("equipment", "UV lamp"),
            categorical("equipment", "紫外灯"),
            "supported",
            "Existing explicitly curated bilingual equipment identity remains supported.",
        ),
        (
            "unfamiliar_equipment_case",
            categorical("equipment", "XQ-a"),
            categorical("equipment", "XQ-A"),
            "unknown",
            "A registered parameter does not make every novel resource value case insensitive.",
        ),
        (
            "unfamiliar_equipment_exact",
            categorical("equipment", "XQ-a"),
            categorical("equipment", "XQ-a"),
            "supported",
            "The complete exact resource value is explicitly declared.",
        ),
        (
            "unrelated_equipment_negative",
            categorical("equipment", "spectrophotometer"),
            categorical("equipment", "UV lamp", availability="unsupported"),
            "unknown",
            "Absence of a UV lamp does not establish absence of a spectrophotometer.",
        ),
        (
            "same_equipment_negative",
            categorical("equipment", "UV lamp"),
            categorical("equipment", "紫外灯", availability="unsupported"),
            "gap",
            "The requested resource itself is explicitly unavailable.",
        ),
        (
            "partial_required_set_negative",
            categorical("equipment", "UV lamp; spectrophotometer"),
            categorical("equipment", "UV lamp", availability="unsupported"),
            "gap",
            "One explicitly required member is unavailable, so the whole set is not covered.",
        ),
        (
            "unrelated_material_negative",
            categorical("specimen_material", "steel", operator="text"),
            categorical("specimen_material", "aluminium", availability="unsupported"),
            "unknown",
            "Unavailability for aluminium is not a statement about steel.",
        ),
        (
            "same_material_negative",
            categorical("specimen_material", "aluminum", operator="text"),
            categorical("specimen_material", "铝", availability="unsupported"),
            "gap",
            "A curated equivalent requested material is explicitly unavailable.",
        ),
        (
            "unrelated_custom_resource_negative",
            categorical("custom_probe", "QX-9"),
            categorical("custom_probe", "QX-7", availability="unsupported"),
            "unknown",
            "A negative declaration cannot broaden from one unknown resource to another.",
        ),
        (
            "same_custom_resource_negative",
            categorical("custom_probe", "QX-9"),
            categorical("custom_probe", "QX-9", availability="unsupported"),
            "gap",
            "An exact custom resource can be explicitly unavailable.",
        ),
        (
            "numeric_negative_unrelated_point",
            numeric(value=60),
            numeric(value=30, availability="unsupported"),
            "unknown",
            "Inability at a recorded 30 °C point establishes nothing about 60 °C.",
        ),
        (
            "numeric_negative_same_point",
            numeric(value=60),
            numeric(value=60, availability="unsupported"),
            "gap",
            "The requested operating point is explicitly unavailable.",
        ),
        (
            "numeric_negative_disjoint_range",
            numeric(value=60),
            numeric(
                operator="range", value=None, minimum=20, maximum=40, availability="unsupported"
            ),
            "unknown",
            "A negative range cannot be broadened to an unrelated requested point.",
        ),
        (
            "numeric_negative_overlapping_required_range",
            numeric(operator="range", value=None, minimum=30, maximum=50),
            numeric(
                operator="range", value=None, minimum=20, maximum=40, availability="unsupported"
            ),
            "gap",
            "Some of the explicitly requested full range is declared unavailable.",
        ),
        (
            "numeric_negative_unit_conversion",
            numeric(value=140, unit="°F"),
            numeric(
                operator="range", value=None, minimum=55, maximum=65, availability="unsupported"
            ),
            "gap",
            "The same unavailable point is recognized through exact unit conversion.",
        ),
        (
            "numeric_negative_incompatible_unit",
            numeric(value=60),
            numeric(value=60, unit="mm", availability="unsupported"),
            "unknown",
            "An invalid dimensional pairing cannot establish a capability gap.",
        ),
        (
            "numeric_negative_missing_boundary",
            numeric(value=60),
            numeric(
                operator="range", value=None, minimum=20, maximum=None, availability="unsupported"
            ),
            "unknown",
            "A missing boundary does not establish whether the requested point is unavailable.",
        ),
        (
            "generic_temperature_negative",
            numeric(value=60),
            numeric(
                operator="text",
                value=None,
                text_value="Not available",
                availability="unsupported",
                raw_text="Temperature control is unavailable.",
            ),
            "gap",
            "A genuinely unqualified negative still establishes unavailability.",
        ),
        (
            "reporting_negative_disjoint_range",
            numeric("gloss", kind="criterion", value=85, unit="GU"),
            numeric(
                "gloss",
                kind="reporting",
                operator="range",
                value=None,
                minimum=0,
                maximum=10,
                unit="GU",
                availability="unsupported",
            ),
            "unknown",
            "Inability to report another scale interval does not establish "
            "inability at this threshold.",
        ),
        (
            "custom_numeric_negative_disjoint_range",
            numeric("custom_force", value=12, unit="N"),
            numeric(
                "custom_force",
                operator="range",
                value=None,
                minimum=1,
                maximum=5,
                unit="N",
                availability="unsupported",
            ),
            "unknown",
            "A custom quantity's unavailable interval is also scoped, not a global negative.",
        ),
    ]


def decision(request, declaration):
    result = compare_requirement(
        NormalizedRequirement(
            requirement_id="req_safety", test_name="Synthetic safety case", claims=[request]
        ),
        [declaration],
    )
    return result.checks[-1]


@pytest.mark.parametrize(
    "case_id,requested,lab,expected,rationale",
    cases(),
    ids=lambda x: x if isinstance(x, str) and " " not in x else None,
)
def test_typed_negative_scope_and_resource_identity(case_id, requested, lab, expected, rationale):
    before = deepcopy((requested.model_dump(), lab.model_dump()))
    check = decision(requested, lab)
    assert check.status == expected, f"{case_id}: {rationale}; got {check.reason}"
    assert (requested.model_dump(), lab.model_dump()) == before


def test_generic_reporting_negative_keeps_existing_asymmetric_context_protection():
    request = numeric(
        "gloss_retention",
        kind="criterion",
        stage="after_cycle",
        operator=">=",
        value=90,
        unit="%",
        source_context="each panel's original gloss",
    )
    lab = numeric(
        "gloss_retention",
        kind="reporting",
        stage="after_cycle",
        operator="required",
        value=None,
        unit="%",
        text_value="Not reportable",
        availability="unsupported",
        raw_text="Cannot report post-cycle gloss retention.",
    )
    assert decision(request, lab).status == "gap"
    scoped = lab.model_copy(update={"source_context": "steel panel"})
    assert decision(request, scoped).status == "unknown"


def test_registered_direct_pair_and_structured_comparison_agree_on_unrelated_negative():
    requested = categorical("specimen_material", "steel", operator="text")
    declared = categorical("specimen_material", "aluminium", availability="unsupported")
    assert (
        evaluate_pair(requested, declared).status
        == decision(requested, declared).status
        == "unknown"
    )


def test_named_reference_panel_negative_cannot_broaden_to_another_reference():
    requested = categorical("reference_panel", "Master AX-9")
    declared = categorical("reference_panel", "Master AX-7", availability="unsupported")
    assert decision(requested, declared).status == "unknown"
    same = declared.model_copy(update={"text_value": "Master AX-9"})
    assert decision(requested, same).status == "gap"
    generic = declared.model_copy(update={"text_value": "Not available"})
    assert (
        decision(requested.model_copy(update={"text_value": "Master panel"}), generic).status
        == "gap"
    )


def test_unregistered_reference_identity_preserves_case_for_a_negative():
    requested = categorical("reference_panel", "Master AX-a")
    declared = categorical("reference_panel", "Master AX-A", availability="unsupported")
    assert decision(requested, declared).status == "unknown"


def test_unresolved_custom_declaration_cannot_establish_negative_availability():
    requested = categorical("custom_probe", "QX-9")
    declared = categorical(
        "custom_probe", "QX-9", availability="unsupported", normalization_state="unresolved"
    )
    assert decision(requested, declared).status == "unknown"


@pytest.mark.parametrize("context", ["Polymer-A", "on Polymer-A", "Batch7", "using XQ+A"])
def test_code_like_context_does_not_invent_case_equivalence(context):
    requested = numeric(source_context=context)
    declared = numeric(source_context=context.casefold())
    assert decision(requested, declared).status == "unknown"
    assert decision(requested, requested.model_copy(update={"id": "lab"})).status == "supported"


@pytest.mark.parametrize(
    "first,second", [("Steel", "steel"), ("STEEL PANEL", "钢板"), ("Steel only", "STEEL ONLY")]
)
def test_curated_material_and_ordinary_prose_case_remain_compatible(first, second):
    assert (
        decision(numeric(source_context=first), numeric(source_context=second)).status
        == "supported"
    )


@pytest.mark.parametrize(
    "requested,unavailable,expected",
    [
        ({"value": 40}, {"values": [20, 60]}, "unknown"),
        ({"value": 60}, {"values": [20, 60]}, "gap"),
        (
            {"operator": "range", "value": None, "minimum": 30, "maximum": 50},
            {"values": [20, 60]},
            "unknown",
        ),
        (
            {"operator": "range", "value": None, "minimum": 30, "maximum": 70},
            {"values": [20, 60]},
            "gap",
        ),
        (
            {"operator": "one_of", "value": None, "values": [10, 40]},
            {"operator": "range", "minimum": 20, "maximum": 30},
            "unknown",
        ),
        (
            {"operator": "one_of", "value": None, "values": [10, 25]},
            {"operator": "range", "minimum": 20, "maximum": 30},
            "gap",
        ),
    ],
)
def test_discrete_unavailability_does_not_fill_intermediate_points(
    requested, unavailable, expected
):
    declared = {"operator": "one_of", "value": None, "availability": "unsupported", **unavailable}
    assert decision(numeric(**requested), numeric(**declared)).status == expected
