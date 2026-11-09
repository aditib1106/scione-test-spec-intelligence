"""Policy tests use explicit typed facts, not model-generated verdicts."""

from decimal import Decimal

import pytest

from scione.parameters import (
    contexts_match,
    definitions,
    evaluate_pair,
    generic_reporting_unavailable,
    normalization_evidence,
    normalize_claim,
    resolve_definition,
)
from scione.structured.models import AtomicClaim


def claim(parameter="temperature", **changes):
    values = dict(
        id="fact",
        parameter_id=parameter,
        label=parameter or "Unresolved",
        operator="=",
        value=60,
        unit="°C",
        raw_text="Source statement",
    )
    values.update(changes)
    return AtomicClaim(**values)


def assess(request, lab):
    result = evaluate_pair(request, lab)
    assert result is not None
    return result.status


def envelope(parameter="temperature", low=20, high=80, **changes):
    return claim(parameter, operator="range", value=None, minimum=low, maximum=high, **changes)


@pytest.mark.parametrize("alias", ["temperature", "temp", "温度", "度温", "temprature"])
def test_approved_exact_aliases(alias):
    assert resolve_definition(alias).id == "temperature"
    assert assess(claim(alias), envelope()) == "supported"


def test_registry_has_stable_unique_ids_and_ui_metadata():
    registry = definitions()
    assert len({item.id for item in registry}) == len(registry)
    assert all(item.label and item.description and item.allowed_roles for item in registry)
    assert resolve_definition("water_temperature").subject == "water"
    assert resolve_definition("measurement_resolution").rule == "lower_is_better"


def test_normalization_copies_source_and_never_reinterprets_unknown_custom_id():
    original = claim(
        "温度", unit="Celsius", source_pages=[6], source_context="curing", stage="固化"
    )
    before = original.model_dump()
    normalized = normalize_claim(original)
    assert normalized.parameter_id == "temperature"
    assert normalized.stage == "curing"
    assert normalized.unit == "°C"
    assert normalized.raw_text == original.raw_text
    assert normalized.source_context == original.source_context
    assert normalized.value == Decimal(60)
    normalized.source_pages.append(9)
    assert original.model_dump() == before
    assert normalization_evidence(original)["semantic_inference"] is False
    assert normalization_evidence(original)["changes"]["parameter_id"]["source"] == "温度"
    custom = claim("custom_a", label="Temperature")
    assert normalize_claim(custom).parameter_id == "custom_a"
    assert evaluate_pair(custom, envelope()) is None


def test_unresolved_source_never_becomes_supported():
    request = claim("temperature", normalization_state="unresolved")
    assert normalize_claim(request).normalization_state == "unresolved"
    assert assess(request, envelope()) == "unknown"


@pytest.mark.parametrize(
    "requested,lab,expected",
    [
        (claim(value=140, unit="°F"), envelope(low=60, high=60), "supported"),
        (claim(value=140.18, unit="°F"), envelope(low=60, high=60), "gap"),
        (claim(value=273.15, unit="K"), envelope(low=0, high=0), "supported"),
        (
            claim("temperature_difference", value=18, unit="°F"),
            envelope("temperature_difference", low=10, high=10, unit="Δ°C"),
            "supported",
        ),
        (
            claim("duration", value=1, unit="h"),
            envelope("duration", low=3600, high=3600, unit="s"),
            "supported",
        ),
        (
            claim("distance", value=1, unit="in"),
            envelope("distance", low="2.54", high="2.54", unit="cm"),
            "supported",
        ),
        (
            claim("load", value=500, unit="g"),
            envelope("load", low="0.4", high="0.6", unit="kg"),
            "supported",
        ),
        (
            claim("pressure", value=1, unit="bar"),
            envelope("pressure", low=100, high=100, unit="kPa"),
            "supported",
        ),
        (
            claim("relative_humidity", value=50, unit="%"),
            envelope("relative_humidity", low=40, high=60, unit="% RH"),
            "supported",
        ),
        (
            claim("cycles", value=5, unit=None),
            envelope("cycles", low=1, high=10, unit="cycles"),
            "supported",
        ),
        (claim("load", value=5, unit="N"), envelope("load", low=0, high=10, unit="g"), "unknown"),
        (claim(unit=None), envelope(), "unknown"),
        (claim(unit="unknown_unit"), envelope(unit="unknown_unit"), "unknown"),
        (
            claim("measurement_angle", unit="radian", value=1),
            envelope("measurement_angle", low=0, high=90, unit="°"),
            "unknown",
        ),
    ],
)
def test_exact_units_and_dimension_guards(requested, lab, expected):
    assert assess(requested, lab) == expected


def test_generic_temperature_never_becomes_water_or_air_implicitly():
    generic = claim()
    water = envelope("water_temperature")
    air = envelope("air_temperature")
    assert assess(generic, water) == "unknown"
    assert assess(generic, air) == "unknown"
    assert assess(claim("water_temperature"), air) == "unknown"
    explicit_label = claim(label="水温")
    assert normalize_claim(explicit_label).parameter_id == "water_temperature"
    assert assess(explicit_label, water) == "supported"
    assert not contexts_match(claim(source_context="water"), envelope(source_context="air"))


def test_legacy_parser_generic_temperature_retains_explicit_raw_subject():
    water = claim(raw_text="Water temperature 60 °C")
    air = envelope(raw_text="Air temperature is adjustable from 20–80 °C")
    assert normalize_claim(water).parameter_id == "water_temperature"
    assert normalize_claim(air).parameter_id == "air_temperature"
    assert assess(water, air) == "unknown"
    assert assess(water, envelope(raw_text="General range 20–80 °C")) == "unknown"
    assert assess(water, envelope("water_temperature")) == "supported"
    assert normalize_claim(claim(raw_text="60 °C water")).parameter_id == "water_temperature"
    assert normalize_claim(claim(source_context="水")).parameter_id == "water_temperature"
    # An activity name cannot establish the medium of a temperature setting.
    assert (
        normalize_claim(claim(raw_text="Water jet test: run at 60 °C")).parameter_id
        == "temperature"
    )


def test_mixed_or_contradictory_temperature_subjects_do_not_certify_a_number():
    mixed = claim(raw_text="Water temperature 60 °C and air temperature 25 °C")
    assert assess(mixed, envelope()) == "unknown"
    assert assess(mixed, mixed) == "unknown"
    contradictory = claim("water_temperature", source_context="air", raw_text="60 °C")
    assert assess(contradictory, contradictory) == "unknown"


@pytest.mark.parametrize(
    "changes",
    [
        {"stage": "conditioning"},
        {"source_context": "aluminium panel"},
        {"sequence": 2},
        {"configuration": "route_b"},
    ],
)
def test_same_number_does_not_erase_stage_material_sequence_or_configuration(changes):
    request = claim(
        stage="curing", source_context="steel panel", sequence=1, configuration="route_a"
    )
    lab_fields = dict(
        stage="curing", source_context="steel panel", sequence=1, configuration="route_a"
    )
    lab_fields.update(changes)
    assert assess(request, envelope(**lab_fields)) == "unknown"


def test_curated_context_and_stage_translation_matches():
    request = claim(stage="固化", source_context="铝板")
    lab = envelope(stage="curing", source_context="aluminum panel")
    assert assess(request, lab) == "supported"
    assert (
        assess(claim(stage="固化", source_context="固化"), envelope(stage="curing")) == "supported"
    )


@pytest.mark.parametrize(
    "requested,lab,expected",
    [
        (claim(value=60, tolerance=5), envelope(low=55, high=65), "supported"),
        (claim(value=60, tolerance=5), envelope(low=56, high=65), "gap"),
        (
            claim(operator="one_of", value=None, values=[20, 60]),
            envelope(low=20, high=60),
            "supported",
        ),
        (claim(value=40), claim(operator="one_of", value=None, values=[20, 60]), "gap"),
        (envelope(low=20, high=60), claim(operator="one_of", value=None, values=[20, 60]), "gap"),
        (claim(value=60), envelope(low=None, high=80), "unknown"),
        (claim(value=90), envelope(low=None, high=80), "gap"),
        (claim(operator="<=", value=60), envelope(), "unknown"),
        (claim(operator="<", value=60), envelope(), "unknown"),
        (claim(), claim(operator=">=", value=20), "unknown"),
    ],
)
def test_numeric_envelope_coverage_is_not_numeric_similarity(requested, lab, expected):
    assert assess(requested, lab) == expected


@pytest.mark.parametrize(
    "requested,lab,expected",
    [
        (
            claim("measurement_resolution", operator="<=", value="0.1", unit="mm"),
            claim("measurement_resolution", value="0.05", unit="mm"),
            "supported",
        ),
        (
            claim("measurement_resolution", operator="<=", value="0.1", unit="mm"),
            claim("measurement_resolution", value="0.2", unit="mm"),
            "gap",
        ),
        (
            claim("measurement_resolution", operator="<", value="0.1", unit="mm"),
            claim("measurement_resolution", value="0.1", unit="mm"),
            "gap",
        ),
        (
            claim("measurement_resolution", operator="<=", value=1, unit="°C"),
            claim("measurement_resolution", value="1.8", unit="°F"),
            "supported",
        ),
        (
            claim("measurement_resolution", operator="<=", value="0.1", unit="mm"),
            claim("measurement_resolution", operator="<=", value="0.2", unit="mm"),
            "unknown",
        ),
        (
            claim("measurement_resolution", operator="<=", value="0.1", unit="mm"),
            claim("measurement_resolution", value="-0.05", unit="mm"),
            "unknown",
        ),
    ],
)
def test_resolution_smaller_is_better_and_temperature_offsets_do_not_apply(
    requested, lab, expected
):
    assert assess(requested, lab) == expected


def test_specimen_threshold_only_establishes_reportability():
    request = claim(
        "color_difference",
        kind="criterion",
        stage="after_cycle",
        operator="<=",
        value=1,
        unit="ΔE*ab",
    )
    lab = envelope("color_difference", kind="reporting", low=0, high=10, unit="ΔEab")
    result = evaluate_pair(request, lab)
    assert result.status == "supported"
    assert "pass/fail is not predicted" in result.reason
    assert assess(request, lab.model_copy(update={"kind": "criterion"})) == "unknown"
    assert assess(request, lab.model_copy(update={"unit": "ΔE00"})) == "unknown"
    assert assess(request, lab.model_copy(update={"availability": "unsupported"})) == "gap"


@pytest.mark.parametrize("lab_value", ["flat_panel", "flat panel", "平板"])
def test_category_aliases_are_parameter_scoped(lab_value):
    request = claim("panel_shape", operator="text", value=None, unit=None, text_value="flat panel")
    lab = claim("panel_shape", operator="text", value=None, unit=None, text_value=lab_value)
    assert assess(request, lab) == "supported"


def test_missing_category_does_not_mean_explicit_inability():
    request = claim(
        "specimen_material", operator="text", value=None, unit=None, text_value="aluminium"
    )
    lab = claim(
        "specimen_material",
        operator="text",
        value=None,
        unit=None,
        text_value="steel",
        raw_text="Steel only",
    )
    assert assess(request, lab) == "unknown"
    assert assess(request, lab.model_copy(update={"availability": "unsupported"})) == "gap"


def test_set_inclusion_uses_explicit_entries_and_does_not_split_prose_commas():
    request = claim(
        "equipment",
        operator="required",
        value=None,
        unit=None,
        text_value="UV lamp; spectrophotometer",
    )
    lab = claim(
        "equipment", operator="text", value=None, unit=None, text_value="紫外灯\n分光光度计\n恒温箱"
    )
    assert assess(request, lab) == "supported"
    assert assess(request, lab.model_copy(update={"text_value": "紫外灯"})) == "unknown"
    assert (
        assess(request, lab.model_copy(update={"text_value": "UV lamp, spectrophotometer"}))
        == "unknown"
    )


def test_boolean_presence_is_explicit_and_context_specific():
    request = claim(
        "reference_panel", operator="required", value=None, unit=None, text_value="available"
    )
    lab = claim("reference_panel", operator="required", value=None, unit=None, text_value="有")
    assert assess(request, lab) == "supported"
    assert assess(request, lab.model_copy(update={"availability": "unknown"})) == "unknown"
    assert assess(request, lab.model_copy(update={"text_value": "possibly available"})) == "unknown"
    assert (
        assess(
            request.model_copy(update={"text_value": "Master panel"}),
            lab.model_copy(update={"text_value": "Master panel"}),
        )
        == "supported"
    )


def test_instruction_and_numeric_prose_do_not_gain_capability_semantics():
    request = claim(operator="text", value=None, text_value="about sixty, maybe")
    assert assess(request, envelope()) == "unknown"
    assert assess(claim(), envelope(kind="instruction")) == "unknown"


def test_generic_boolean_does_not_guarantee_a_named_customer_resource():
    requested = claim(
        "reference_panel",
        operator="required",
        value=None,
        unit=None,
        text_value="Customer-approved master panel BMW-Z2",
    )
    declared = claim(
        "reference_panel", operator="required", value=None, unit=None, text_value="available"
    )
    assert assess(requested, declared) == "unknown"


def test_nominal_tolerance_is_not_silently_a_selectable_lab_range():
    nominal = claim(value=60, tolerance=5)
    assert assess(claim(value=62), nominal) == "unknown"
    assert assess(claim(value=60, tolerance=5), nominal) == "supported"
    assert assess(envelope(low=55, high=65), nominal) == "unknown"
    assert assess(claim(value=60, tolerance=4), nominal) == "unknown"
    assert assess(claim(value=62, tolerance=7), nominal) == "unknown"
    assert assess(claim(value=62), envelope(low=55, high=65)) == "supported"


def test_same_fixed_nominal_tolerance_compares_offsets_and_deltas_separately():
    requested = claim(value=23, tolerance=2, unit="°C")
    fahrenheit = claim(value="73.4", tolerance="3.6", unit="°F")
    assert assess(requested, fahrenheit) == "supported"
    assert (
        assess(requested, fahrenheit.model_copy(update={"tolerance": Decimal("3.7")})) == "unknown"
    )
    assert assess(requested, claim(value=23, tolerance=1, unit="°C")) == "supported"
    assert assess(claim(value=23, tolerance=0, unit="°C"), fahrenheit) == "unknown"


@pytest.mark.parametrize("unit", ["mPa", "MPA", "mpa"])
def test_si_prefix_case_cannot_be_folded_into_another_scale(unit):
    requested = claim("pressure", value=1, unit=unit)
    assert assess(requested, envelope("pressure", low=0, high=10, unit="MPa")) == "unknown"
    assert normalize_claim(requested).unit == unit


def test_si_length_symbol_case_is_distinct_but_word_aliases_are_case_insensitive():
    assert (
        assess(
            claim("distance", unit="Mm", value=1), envelope("distance", low=0, high=10, unit="mm")
        )
        == "unknown"
    )
    assert assess(claim(unit="CELSIUS"), envelope()) == "supported"


def test_unknown_category_punctuation_is_not_a_semantic_alias():
    requested = claim(
        "specimen_material", operator="text", value=None, unit=None, text_value="XQ-7"
    )
    declared = claim("specimen_material", operator="text", value=None, unit=None, text_value="XQ+7")
    assert assess(requested, declared) == "unknown"
    assert assess(requested, declared.model_copy(update={"text_value": "XQ-7"})) == "supported"
    panel = requested.model_copy(update={"parameter_id": "panel_shape", "text_value": "flat+panel"})
    assert assess(panel, panel.model_copy(update={"text_value": "flat panel"})) == "unknown"


def test_unknown_material_context_keeps_mixture_versus_alternative_punctuation():
    assert assess(claim(source_context="A/B"), envelope(source_context="A+B")) == "unknown"
    assert assess(claim(source_context="A/B"), envelope(source_context="A/B")) == "supported"


def reportability_pair(**lab_changes):
    requested = claim(
        "gloss_retention",
        kind="criterion",
        stage="after_cycle",
        operator=">=",
        value=90,
        unit="%",
        source_context="retention of its own original value after cycling",
        raw_text="After cycling, retain at least 90% of each panel's own original gloss.",
    )
    declared = claim(
        "gloss_retention",
        kind="reporting",
        stage="after_cycle",
        operator="required",
        value=None,
        text_value="Not reportable",
        availability="unsupported",
        unit="%",
        raw_text="Cannot report post-cycle gloss retention.",
        **lab_changes,
    )
    return requested, declared


def test_generic_explicit_reporting_negative_applies_to_more_qualified_customer_output():
    requested, declared = reportability_pair()
    before = requested.model_dump(), declared.model_dump()
    assert generic_reporting_unavailable(requested, declared)
    assert contexts_match(requested, declared)
    assert assess(requested, declared) == "gap"
    assert "more specifically qualified" in evaluate_pair(requested, declared).reason
    assert (requested.model_dump(), declared.model_dump()) == before


def test_generic_reporting_negative_is_not_a_generic_positive():
    requested, declared = reportability_pair()
    supported = declared.model_copy(update={"availability": "supported"})
    assert not generic_reporting_unavailable(requested, supported)
    assert assess(requested, supported) == "unknown"


@pytest.mark.parametrize(
    "changes",
    [
        {"source_context": "steel panel"},
        {"configuration": "route_a"},
        {"sequence": 1},
        {"normalization_state": "unresolved"},
        {"stage": "initial"},
        {"kind": "condition"},
        {"raw_text": "Cannot report gloss retention on steel panels."},
    ],
)
def test_scoped_or_unresolved_negative_cannot_be_promoted_to_global(changes):
    requested, declared = reportability_pair()
    declared = declared.model_copy(update=changes)
    assert not generic_reporting_unavailable(requested, declared)
    assert assess(requested, declared) == "unknown"


def test_native_output_general_negative_and_requested_configuration_remain_asymmetric():
    requested, declared = reportability_pair()
    declared = declared.model_copy(update={"stage": "general"})
    requested = requested.model_copy(update={"configuration": "customer_route", "sequence": 3})
    assert generic_reporting_unavailable(requested, declared)
    assert assess(requested, declared) == "gap"


def test_generic_reporting_negative_never_crosses_distinct_media_or_claim_roles():
    requested = claim("water_temperature", kind="criterion", source_context="Water", value=60)
    declared = claim(
        "air_temperature",
        kind="reporting",
        value=None,
        operator="required",
        availability="unsupported",
        raw_text="Cannot report air temperature.",
    )
    assert not generic_reporting_unavailable(requested, declared)
    assert assess(requested, declared) == "unknown"
    requested, declared = reportability_pair()
    assert assess(requested.model_copy(update={"kind": "condition"}), declared) == "unknown"


def test_scope_in_unparsed_negative_prose_is_not_global_even_when_both_contexts_missing():
    requested, declared = reportability_pair()
    requested = requested.model_copy(update={"source_context": None})
    declared = declared.model_copy(update={"raw_text": "Cannot report gloss retention on steel."})
    assert assess(requested, declared) == "unknown"
