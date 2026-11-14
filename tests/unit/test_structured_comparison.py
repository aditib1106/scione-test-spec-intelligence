"""Business comparisons and source grounding, without model or database calls."""

from decimal import Decimal

import pytest

from scione.catalogs.models import LabCapability
from scione.catalogs.seed import default_csp301_lab
from scione.specifications.extraction import _expand_compact_extraction
from scione.specifications.models import (
    CompactCustomerExtraction,
    CustomerRequirement,
    RequirementEvidence,
    RequirementValue,
)
from scione.structured import (
    AtomicClaim,
    NormalizedRequirement,
    compare_requirement,
    normalize_capability,
    normalize_requirement,
)


def requirement(*, conditions=(), criteria=(), instructions=(), atoms=(), name="Test"):
    return CustomerRequirement(
        id="req_test",
        original_test_name=name,
        conditions=[RequirementValue(name="Condition", raw_value=value) for value in conditions],
        acceptance_criteria=[
            RequirementValue(name="Criterion", raw_value=value) for value in criteria
        ],
        special_instructions=list(instructions),
        atomic_claims=list(atoms),
        evidence=[
            RequirementEvidence(
                page=1,
                quote="\n".join([*conditions, *criteria, *instructions]) or "Customer test source",
            )
        ],
    )


def atom(**updates):
    fields = dict(
        id="atom_1",
        parameter_id="temperature",
        label="Temperature",
        kind="condition",
        stage="conditioning",
        operator="=",
        value=Decimal(23),
        unit="°C",
        raw_text="23 °C",
        source_pages=[1],
    )
    fields.update(updates)
    return AtomicClaim(**fields)


def comparison(requested, declared):
    return compare_requirement(
        NormalizedRequirement(requirement_id="req_test", test_name="Test", claims=requested),
        declared,
    )


def test_compound_condition_splits_into_fields_and_preserves_original_source():
    raw = "Condition for seven days at 23 ± 2 °C and 50 ± 5% RH."
    normalized = normalize_requirement(requirement(conditions=[raw]))
    claims = {item.parameter_id: item for item in normalized.claims}
    assert set(claims) == {"duration", "temperature", "relative_humidity"}
    assert claims["duration"].value == 7
    assert claims["duration"].unit == "days"
    assert claims["temperature"].value == 23
    assert claims["temperature"].tolerance == 2
    assert claims["relative_humidity"].value == 50
    assert all(claim.stage == "conditioning" for claim in claims.values())
    assert all(claim.raw_text == raw and claim.source_pages == [1] for claim in claims.values())


def test_cycle_sequence_is_not_flattened_into_temperature_range():
    raw = "5 cycles: -20 °C for 2 h and 70 °C for 2 h; recovery for 30 min between transfers."
    normalized = normalize_requirement(requirement(conditions=[raw]))
    temperatures = [claim for claim in normalized.claims if claim.parameter_id == "temperature"]
    assert [(claim.value, claim.sequence) for claim in temperatures] == [(-20, 1), (70, 2)]
    assert all(claim.operator == "=" for claim in temperatures)
    assert any(claim.value == 30 and claim.unit == "min" for claim in normalized.claims)


def test_temperature_at_another_stage_cannot_satisfy_conditioning():
    result = comparison([atom()], [atom(id="lab", stage="curing", value=Decimal(23))])
    assert result.status == "needs_information"
    assert result.checks[-1].status == "unknown"


def test_tolerance_entire_range_must_fit_lab_limits():
    request = atom(tolerance=Decimal(2))
    declared = atom(
        id="lab", operator="range", value=None, minimum=Decimal(22), maximum=Decimal(25)
    )
    result = comparison([request], [declared])
    assert result.checks[-1].status == "gap"
    assert result.status == "partially_matched"


def test_duration_conversion_uses_exact_rational_values():
    requested = atom(parameter_id="duration", value=Decimal(30), unit="min")
    declared = atom(parameter_id="duration", value=Decimal("0.5"), unit="h", id="lab")
    assert comparison([requested], [declared]).status == "fully_matched"
    requested = requested.model_copy(update={"value": Decimal(60), "unit": "s"})
    declared = declared.model_copy(update={"value": Decimal(1), "unit": "min"})
    assert comparison([requested], [declared]).status == "fully_matched"


@pytest.mark.parametrize(
    ("parameter", "unit"),
    [("cycles", "cycles"), ("measurement_locations", "locations")],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_intrinsic_count_does_not_require_redundant_unit_label(parameter, unit, reverse):
    requested = atom(parameter_id=parameter, value=Decimal(5), unit=None)
    declared = atom(id="lab", parameter_id=parameter, value=Decimal(5), unit=unit)
    if reverse:
        requested, declared = declared, requested
    assert comparison([requested], [declared]).checks[-1].status == "supported"


@pytest.mark.parametrize("parameter", ["cycles", "measurement_locations", "duration"])
def test_missing_unit_does_not_cover_an_unrelated_physical_unit(parameter):
    requested = atom(parameter_id=parameter, value=Decimal(5), unit=None)
    declared = atom(id="lab", parameter_id=parameter, value=Decimal(5), unit="h")
    assert comparison([requested], [declared]).checks[-1].status == "unknown"


def test_explicit_post_cycle_gloss_limitation_is_a_gap():
    lab = next(cap for cap in default_csp301_lab().capabilities if cap.id == "cap_csp301_gloss")
    normalized = normalize_requirement(
        requirement(
            name="Specular gloss",
            criteria=[
                "Initial gloss 82–92 GU",
                "After cycling retain at least 90% of its original value",
            ],
        )
    )
    result = compare_requirement(normalized, normalize_capability(lab))
    checks = {check.parameter_id: check for check in result.checks}
    assert checks["gloss"].status == "supported"
    assert checks["gloss_retention"].status == "gap"
    assert result.status == "partially_matched"


def test_initial_gloss_does_not_cover_post_cycle_gloss():
    requested = atom(
        parameter_id="gloss", kind="criterion", stage="after_cycle", value=Decimal(90), unit="GU"
    )
    declared = atom(
        id="lab",
        parameter_id="gloss",
        kind="reporting",
        stage="initial",
        value=None,
        operator="range",
        minimum=Decimal(0),
        maximum=Decimal(200),
        unit="GU",
    )
    assert comparison([requested], [declared]).checks[-1].status == "unknown"


def test_generic_thickness_range_does_not_establish_topcoat_layer_measurement():
    lab = next(cap for cap in default_csp301_lab().capabilities if cap.id == "cap_csp301_thickness")
    normalized = normalize_requirement(
        requirement(name="Dry film thickness", criteria=["Topcoat thickness 42–55 µm"])
    )
    result = compare_requirement(normalized, normalize_capability(lab))
    assert result.checks[-1].parameter_id == "topcoat_thickness"
    assert result.checks[-1].status == "unknown"


def test_colour_threshold_can_be_measured_but_d65_and_master_remain_missing():
    lab = next(cap for cap in default_csp301_lab().capabilities if cap.id == "cap_csp301_color")
    normalized = normalize_requirement(
        requirement(
            name="Colour difference",
            conditions=["Compare with an untouched master under D65 lighting"],
            criteria=["ΔE*ab ≤1.0"],
        )
    )
    result = compare_requirement(normalized, normalize_capability(lab))
    checks = {check.parameter_id: check for check in result.checks}
    assert checks["color_difference"].status == "supported"
    assert "specimen pass/fail is not predicted" in checks["color_difference"].reason
    assert checks["illuminant"].status == "unknown"
    assert checks["reference_panel"].status == "unknown"
    assert result.status == "needs_information"


def test_ordinal_acceptance_is_not_material_pass_prediction():
    lab = next(cap for cap in default_csp301_lab().capabilities if cap.id == "cap_csp301_cross_cut")
    normalized = normalize_requirement(
        requirement(name="Cross-cut adhesion", criteria=["Only class 0 or 1 is acceptable"])
    )
    result = compare_requirement(normalized, normalize_capability(lab))
    assert result.checks[-1].status == "supported"
    assert "specimen pass/fail is not predicted" in result.checks[-1].reason


def test_unrecognized_clause_is_retained_and_not_automatically_supported():
    raw = "Use a certified moon-glass whisker operator"
    normalized = normalize_requirement(requirement(instructions=[raw]))
    assert normalized.claims[0].parameter_id is None
    assert normalized.claims[0].raw_text == raw
    assert comparison(normalized.claims, []).status == "needs_information"


def test_model_atomic_claim_is_grounded_in_source_and_numeric_values():
    good = atom(raw_text="23 °C", stage="general")
    normalized = normalize_requirement(requirement(conditions=["23 °C"], atoms=[good]))
    assert len(normalized.claims) == 1
    assert normalized.claims[0].parameter_id == "temperature"
    bad = good.model_copy(update={"value": Decimal(200)})
    normalized = normalize_requirement(requirement(conditions=["23 °C"], atoms=[bad]))
    assert normalized.claims[0].parameter_id is None
    assert normalized.claims[0].availability == "unknown"
    absent_page = good.model_copy(update={"source_pages": [99]})
    assert (
        normalize_requirement(requirement(conditions=["23 °C"], atoms=[absent_page]))
        .claims[0]
        .parameter_id
        is None
    )


@pytest.mark.parametrize(
    ("raw", "low", "high"),
    [("82-92 GU", "82", "92"), ("-20 to -10 °C", "-20", "-10")],
)
def test_grounding_distinguishes_range_separators_from_negative_endpoints(raw, low, high):
    temperature = "°C" in raw
    supplied = atom(
        parameter_id="temperature" if temperature else "gloss",
        kind="condition" if temperature else "criterion",
        stage="general",
        operator="range",
        value=None,
        minimum=Decimal(low),
        maximum=Decimal(high),
        unit="°C" if temperature else "GU",
        raw_text=raw,
    )
    row = requirement(
        conditions=[raw] if temperature else [],
        criteria=[] if temperature else [raw],
        atoms=[supplied],
    )
    assert normalize_requirement(row).claims[0].normalization_state == "parsed"
    row.atomic_claims = [supplied.model_copy(update={"maximum": Decimal(high) + 1})]
    assert normalize_requirement(row).claims[0].normalization_state == "unresolved"


def test_between_bounds_remain_a_range_instead_of_just_the_second_number():
    raw = "The separate topcoat thickness must be between 42 and 55 µm on every panel."
    claims = normalize_requirement(requirement(criteria=[raw])).claims
    thickness = next(claim for claim in claims if claim.parameter_id == "topcoat_thickness")
    assert thickness.operator == "range"
    assert (thickness.minimum, thickness.maximum) == (42, 55)
    assert thickness.raw_text == raw


def test_explicit_verbal_colour_threshold_is_parsed_without_trusting_rewritten_ai_quote():
    raw = "Overall colour difference, calculated from measured L*, a* and b*, may not exceed 1.0."
    rewritten = atom(
        parameter_id="color_difference",
        kind="criterion",
        stage="measurement",
        operator="<=",
        value=Decimal(1),
        unit="ΔE*ab",
        raw_text="<=1.0",
    )
    row = requirement(name="Colour difference", criteria=[raw], atoms=[rewritten])
    normalized = normalize_requirement(row)
    assert normalized.claims[0].normalization_state == "unresolved"
    grounded = next(
        claim for claim in normalized.claims if claim.parameter_id == "color_difference"
    )
    assert grounded.operator == "<=" and grounded.value == 1
    assert grounded.raw_text == raw
    lab = next(cap for cap in default_csp301_lab().capabilities if cap.id == "cap_csp301_color")
    result = compare_requirement(normalized, normalize_capability(lab))
    assert any(
        check.parameter_id == "color_difference" and check.status == "supported"
        for check in result.checks
    )


def test_model_count_with_omitted_unit_does_not_duplicate_the_local_count():
    supplied = atom(
        parameter_id="cycles",
        kind="condition",
        stage="thermal_cycle",
        value=Decimal(5),
        unit=None,
        raw_text="five cycles",
    )
    row = requirement(conditions=["five cycles"], atoms=[supplied])
    counts = [
        claim for claim in normalize_requirement(row).claims if claim.parameter_id == "cycles"
    ]
    assert len(counts) == 1
    assert counts[0].unit == "cycles"


def test_one_model_atom_does_not_hide_other_parts_of_a_compound_condition():
    supplied = atom(raw_text="23 °C")
    normalized = normalize_requirement(
        requirement(
            conditions=["Condition for 7 days at 23 °C and 50% RH"],
            atoms=[supplied],
        )
    )
    assert {claim.parameter_id for claim in normalized.claims} == {
        "temperature",
        "duration",
        "relative_humidity",
    }


def test_cannot_mix_limits_from_two_lab_configurations():
    requested = atom()
    first = atom(id="lab_a", configuration="setup_a")
    second = atom(id="lab_b", configuration="setup_b")
    result = comparison([requested], [first, second])
    assert result.checks[-1].status == "unknown"
    assert "Select one configuration" in result.checks[-1].reason


def test_conflicting_declarations_are_not_silently_resolved_positive():
    positive = atom(id="lab_yes")
    negative = atom(id="lab_no", availability="unsupported")
    result = comparison([atom()], [positive, negative])
    assert result.checks[-1].status == "unknown"
    assert "conflict" in result.checks[-1].reason


def test_method_unavailability_remains_not_matched():
    result = compare_requirement(
        NormalizedRequirement(requirement_id="req_test", test_name="Test", claims=[atom()]),
        [],
        method_status="gap",
    )
    assert result.status == "not_matched"


def test_compact_atoms_expand_in_same_extraction_without_losing_raw_strings():
    raw = "23 ± 2 °C"
    compact = CompactCustomerExtraction.model_validate(
        {
            "document": {"identifier": "CS-1"},
            "requirements": [
                {
                    "test": "Test",
                    "conditions": [raw],
                    "criteria": [],
                    "evidence": [{"page": 1, "quote": raw}],
                    "atomic_claims": [
                        {
                            "parameter_id": "temperature",
                            "kind": "condition",
                            "stage": "conditioning",
                            "operator": "=",
                            "values": [23, 2],
                            "unit": "°C",
                            "text_value": None,
                            "raw_text": raw,
                            "source_pages": [1],
                            "sequence": None,
                        }
                    ],
                }
            ],
        }
    )
    expanded = _expand_compact_extraction(compact)
    assert expanded.requirements[0].conditions[0].raw_value == raw
    claim = expanded.requirements[0].atomic_claims[0]
    assert claim.value == 23 and claim.tolerance == 2
    assert claim.id == "req_001_claim_1"


def test_one_model_atom_does_not_hide_second_step_of_same_parameter():
    raw = "5 cycles: -20 °C for 2 h and 70 °C for 2 h"
    first = atom(
        parameter_id="temperature",
        stage="thermal_cycle",
        value=Decimal(-20),
        raw_text="-20 °C",
        sequence=1,
    )
    normalized = normalize_requirement(requirement(conditions=[raw], atoms=[first]))
    temperatures = [claim for claim in normalized.claims if claim.parameter_id == "temperature"]
    assert {(claim.value, claim.sequence) for claim in temperatures} == {
        (Decimal(-20), 1),
        (Decimal(70), 2),
    }


def test_model_cannot_relabel_temperature_number_as_hours():
    supplied = atom(parameter_id="duration", unit="h", raw_text="23 °C")
    normalized = normalize_requirement(requirement(conditions=["23 °C"], atoms=[supplied]))
    assert normalized.claims[0].parameter_id is None
    assert normalized.claims[0].normalization_state == "unresolved"


def test_missing_lab_range_endpoint_is_not_assumed_unlimited():
    declared = atom(id="lab", value=None, operator="range", minimum=Decimal(20), maximum=None)
    result = comparison([atom(value=Decimal(200))], [declared])
    assert result.checks[-1].status == "unknown"
    assert "boundary" in result.checks[-1].reason
    result = comparison([atom(value=Decimal(10))], [declared])
    assert result.checks[-1].status == "gap"


def test_intrinsic_measurement_context_does_not_create_false_general_vs_measurement_gap():
    requested = atom(parameter_id="measurement_angle", stage="general", value=Decimal(60), unit="°")
    declared = atom(
        id="lab", parameter_id="measurement_angle", stage="measurement", value=Decimal(60), unit="°"
    )
    assert comparison([requested], [declared]).checks[-1].status == "supported"
    # Unrelated temperature contexts remain separate.
    assert (
        comparison([atom(stage="general")], [atom(id="lab", stage="measurement")]).checks[-1].status
        == "unknown"
    )


def test_lab_measurement_setup_added_in_outputs_resolves_missing_d65_and_master():
    requested = normalize_requirement(
        requirement(
            name="Colour difference",
            conditions=["Compare with an untouched master under D65 lighting"],
        )
    )
    lab = next(cap for cap in default_csp301_lab().capabilities if cap.id == "cap_csp301_color")
    before = compare_requirement(requested, normalize_capability(lab))
    assert {check.parameter_id for check in before.checks if check.status == "unknown"} == {
        "illuminant",
        "reference_panel",
    }
    updated = lab.model_copy(
        update={
            "notes": (lab.notes or "").replace(
                "\nSource:",
                "; D65 lighting; Master-panel comparison.\nSource:",
            )
        }
    )
    declarations = normalize_capability(updated)
    setup = [
        claim for claim in declarations if claim.parameter_id in {"illuminant", "reference_panel"}
    ]
    assert all(claim.kind == "condition" and claim.stage == "measurement" for claim in setup)
    after = compare_requirement(requested, declarations)
    assert after.status == "fully_matched"


def test_explicit_phase_on_lab_measurement_setup_is_not_erased():
    requested = normalize_requirement(requirement(conditions=["D65 lighting"]))
    lab = next(cap for cap in default_csp301_lab().capabilities if cap.id == "cap_csp301_color")
    updated = lab.model_copy(update={"notes": "Reportable outputs: Initial D65 lighting."})
    declared = normalize_capability(updated)
    assert (
        next(claim for claim in declared if claim.parameter_id == "illuminant").stage == "initial"
    )
    assert compare_requirement(requested, declared).checks[-1].status == "unknown"


def test_clause_specific_pages_and_ai_fragment_page_grounding():
    row = requirement(
        name="Specular gloss",
        conditions=["Condition for seven days at 23 °C"],
        criteria=["Initial gloss 82–92 GU"],
    )
    row.evidence = [
        RequirementEvidence(page=1, quote="Condition for seven days at 23 °C"),
        RequirementEvidence(page=2, quote="Initial gloss 82–92 GU"),
    ]
    result = normalize_requirement(row)
    gloss = next(claim for claim in result.claims if claim.parameter_id == "gloss")
    assert gloss.source_pages == [2]
    assert all(claim.source_pages == [1] for claim in result.claims if claim.kind == "condition")
    bad = atom(
        parameter_id="gloss",
        kind="criterion",
        stage="initial",
        value=Decimal(82),
        unit="GU",
        raw_text="82–92 GU",
        source_pages=[1],
    )
    row.atomic_claims = [bad]
    assert normalize_requirement(row).claims[0].normalization_state == "unresolved"


def test_plain_lab_details_need_no_internal_heading_to_record_measurement_setup():
    requested = normalize_requirement(requirement(conditions=["D65 lighting"]))
    lab = next(cap for cap in default_csp301_lab().capabilities if cap.id == "cap_csp301_color")
    updated = lab.model_copy(update={"notes": "D65 lighting; Master-panel comparison."})
    result = compare_requirement(requested, normalize_capability(updated))
    assert result.checks[-1].status == "supported"


def test_parsed_temperature_cannot_hide_unparsed_atmosphere_requirement():
    requested = normalize_requirement(requirement(conditions=["23 °C in nitrogen atmosphere"]))
    lab = LabCapability(id="lab", canonical_test_id="gloss", declared_constraints="23 °C")
    result = compare_requirement(requested, normalize_capability(lab))

    assert result.status == "needs_information"
    assert any(
        check.parameter_id == "temperature" and check.status == "supported"
        for check in result.checks
    )
    remaining = [check for check in result.checks if check.parameter_id is None]
    assert len(remaining) == 1
    assert remaining[0].status == "unknown"
    assert "nitrogen" in remaining[0].requested


def test_grounded_separate_ai_parameter_can_explain_qualifier_fallback_does_not_parse():
    temperature = atom(stage="general", raw_text="23 °C")
    atmosphere = atom(
        parameter_id="test_atmosphere",
        label="Test atmosphere",
        stage="general",
        operator="text",
        value=None,
        unit=None,
        text_value="nitrogen",
        raw_text="nitrogen atmosphere",
    )
    requested = normalize_requirement(
        requirement(conditions=["23 °C in nitrogen atmosphere"], atoms=[temperature, atmosphere])
    )
    declared = [
        temperature.model_copy(update={"id": "lab_temperature"}),
        atmosphere.model_copy(update={"id": "lab_atmosphere"}),
    ]

    assert {claim.parameter_id for claim in requested.claims} == {"temperature", "test_atmosphere"}
    assert compare_requirement(requested, declared).status == "fully_matched"


@pytest.mark.parametrize("extra", ["using fixture GX-7", "仅限铝板", "except oxygen exposure"])
def test_grounded_atmosphere_value_never_owns_neighboring_restrictions(extra):
    raw = f"23 °C in nitrogen atmosphere, {extra}"
    temperature = atom(stage="general", raw_text="23 °C")
    atmosphere = atom(
        parameter_id="test_atmosphere",
        label="Test atmosphere",
        stage="general",
        operator="text",
        value=None,
        unit=None,
        text_value="nitrogen",
        # Deliberately broad: this quotation must not certify the other words.
        raw_text=f"nitrogen atmosphere, {extra}",
    )
    normalized = normalize_requirement(
        requirement(conditions=[raw], atoms=[temperature, atmosphere])
    )
    residuals = [
        claim.text_value or "" for claim in normalized.claims if claim.parameter_id is None
    ]
    assert residuals
    assert all(
        token.casefold() in " ".join(residuals).casefold()
        for token in extra.split()
        if token != "using"
    )
    assert compare_requirement(normalized, [temperature, atmosphere]).status == "needs_information"


def test_ai_temperature_quoting_entire_clause_does_not_claim_to_understand_qualifier():
    supplied = atom(stage="general", raw_text="23 °C in nitrogen atmosphere")
    requested = normalize_requirement(
        requirement(conditions=["23 °C in nitrogen atmosphere"], atoms=[supplied])
    )
    result = compare_requirement(requested, [atom(stage="general", id="lab")])

    assert result.status == "needs_information"
    assert any(claim.parameter_id is None for claim in requested.claims)


def test_unparsed_lab_restriction_cannot_be_discarded_to_certify_numeric_support():
    requested = normalize_requirement(requirement(conditions=["23 °C"]))
    lab = LabCapability(
        id="lab", canonical_test_id="gloss", declared_constraints="23 °C except nitrogen atmosphere"
    )
    result = compare_requirement(requested, normalize_capability(lab))

    assert result.status == "needs_information"
    assert not any(
        check.parameter_id == "temperature" and check.status == "supported"
        for check in result.checks
    )


def test_curing_settings_remain_assessed_when_written_as_special_instructions():
    raw = (
        "Actual metal temperature, not oven setting, is the cure reference: "
        "20 minutes at 140 ± 3 °C."
    )
    requested = normalize_requirement(requirement(instructions=[raw]))
    lab = LabCapability(id="lab", canonical_test_id="gloss", declared_constraints="23 °C")
    result = compare_requirement(requested, normalize_capability(lab))

    assert result.status == "needs_information"
    settings = [
        check for check in result.checks if check.parameter_id in {"temperature", "duration"}
    ]
    assert len(settings) == 2
    assert all(check.stage == "curing" and check.status == "unknown" for check in settings)


def test_simple_curing_setting_can_match_explicit_lab_curing_declaration():
    requested = normalize_requirement(requirement(instructions=["Cure at 140 °C for 20 min"]))
    lab = LabCapability(
        id="lab", canonical_test_id="gloss", declared_constraints="Cure at 140 °C for 20 min"
    )
    result = compare_requirement(requested, normalize_capability(lab))

    assert result.status == "fully_matched"
    assert all(check.status == "supported" for check in result.checks)


def test_workflow_wording_does_not_hide_operating_values_in_same_instruction():
    requested = normalize_requirement(
        requirement(
            instructions=["Report panel-level observations at 100 °C in nitrogen atmosphere"]
        )
    )
    result = compare_requirement(requested, [])

    assert result.status == "needs_information"
    assert any(
        check.parameter_id == "temperature" and check.status == "unknown" for check in result.checks
    )


def test_mixed_humidity_and_retention_percentages_do_not_create_false_humidity_gap():
    requested = normalize_requirement(
        requirement(conditions=["23 °C, 50% RH and at least 90% gloss retention"])
    )
    lab = LabCapability(
        id="lab",
        canonical_test_id="gloss",
        declared_constraints="23 °C; 50% RH",
        notes="Reportable outputs: Gloss retention 0-100%.",
    )
    result = compare_requirement(requested, normalize_capability(lab))

    assert result.status == "needs_information"
    assert not any(check.status == "gap" for check in result.checks)
    assert any(
        check.parameter_id == "temperature" and check.status == "supported"
        for check in result.checks
    )
    assert not any(
        claim.parameter_id == "relative_humidity" and claim.value == Decimal(90)
        for claim in requested.claims
    )


def test_repeated_identical_raw_field_is_normalized_once():
    requested = normalize_requirement(requirement(conditions=["23 °C", "23 °C"]))
    assert len([item for item in requested.claims if item.parameter_id == "temperature"]) == 1


def test_repeated_identical_model_atom_is_normalized_once():
    requested = normalize_requirement(
        requirement(
            conditions=["23 °C"],
            atoms=[atom(stage="general"), atom(id="same_fact_twice", stage="general")],
        )
    )
    assert len([item for item in requested.claims if item.parameter_id == "temperature"]) == 1


def test_equal_model_values_keep_distinct_configurations_and_source_pages():
    source = requirement(
        conditions=["23 °C"],
        atoms=[
            atom(id="water", configuration="water"),
            atom(id="air", configuration="air"),
            atom(id="later_page", configuration="water", source_pages=[2]),
        ],
    )
    source.evidence.append(RequirementEvidence(page=2, quote="23 °C"))
    requested = normalize_requirement(source)
    temperatures = [item for item in requested.claims if item.parameter_id == "temperature"]
    assert len(temperatures) == 3
    assert [item.configuration for item in temperatures] == ["water", "air", "water"]
    assert [item.source_pages for item in temperatures] == [[1], [1], [2]]
    # All original source views remain, but their invented configuration labels
    # cannot establish support merely because a numeric copy was suppressed.
    assert requested.review_issues
    assert compare_requirement(requested, temperatures).status == "needs_information"
    assert [item.source_pages for item in source.atomic_claims] == [[1], [1], [2]]


def test_between_two_cycle_steps_is_syntax_but_its_qualifiers_are_preserved():
    plain = "5 cycles between -20 °C for 2 h and 70 °C for 2 h"
    requested = normalize_requirement(requirement(conditions=[plain]))
    durations = [item for item in requested.claims if item.parameter_id == "duration"]
    assert [(item.value, item.sequence) for item in durations] == [(2, 1), (2, 2)]
    assert not any(item.text_value == "between" for item in requested.claims)
    restricted = normalize_requirement(requirement(conditions=[plain + " in nitrogen atmosphere"]))
    assert any(
        item.normalization_state == "unresolved" and "nitrogen" in item.text_value
        for item in restricted.claims
        if item.text_value
    )
