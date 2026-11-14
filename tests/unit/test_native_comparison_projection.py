"""The business projection deduplicates evidence without hiding source gaps."""

from copy import deepcopy
from decimal import Decimal

import pytest

from scione.specifications.models import (
    CustomerRequirement,
    RequirementEvidence,
    RequirementValue,
)
from scione.structured.comparison import compare_requirement
from scione.structured.models import NORMALIZER_VERSION, AtomicClaim
from scione.structured.normalization import normalize_requirement


def source(raw, atoms=(), *, criteria=False):
    field = "acceptance_criteria" if criteria else "conditions"
    return CustomerRequirement(
        id="req_native_view",
        original_test_name="Synthetic source-coverage test",
        **{field: [RequirementValue(name="Requirement", raw_value=raw)]},
        evidence=[RequirementEvidence(page=1, quote=raw)],
        atomic_claims=list(atoms),
    )


def temperature(raw="23 °C", **updates):
    return AtomicClaim(
        **{
            "id": "native_temperature",
            "parameter_id": "temperature",
            "label": "Temperature",
            "kind": "condition",
            "operator": "=",
            "value": Decimal("23"),
            "unit": "°C",
            "raw_text": raw,
            "source_pages": [1],
            **updates,
        }
    )


def test_mode_is_explicit_auditable_and_does_not_mutate_source():
    row = source("23 °C", [temperature()])
    original = deepcopy(row.model_dump(mode="json"))
    legacy = normalize_requirement(row)
    projected = normalize_requirement(row, native_first=True)
    assert legacy.normalizer_version == NORMALIZER_VERSION
    assert projected.normalizer_version == f"{NORMALIZER_VERSION}_native_first"
    assert projected.claims == legacy.claims
    assert projected.normalization_actions[0]["mode"] == "native_first_v2"
    assert row.model_dump(mode="json") == original


def test_raw_only_legacy_runs_do_not_change_when_opted_in():
    row = source("Condition for 2 h at 23 °C in nitrogen atmosphere.")
    assert normalize_requirement(row, native_first=True) == normalize_requirement(row)


@pytest.mark.parametrize(
    "parameter,label,literal,operator,value,minimum,maximum,unit",
    [
        ("measurement_angle", "Measurement angle", "60°", "=", "60", None, None, "°"),
        ("temperature", "Temperature", "25 °C", "=", "25", None, None, "°C"),
        ("temperature", "Temperature", "20-70 °C", "range", None, "20", "70", "°C"),
    ],
)
def test_complete_native_numeric_text_is_projected_without_changing_source(
    parameter, label, literal, operator, value, minimum, maximum, unit
):
    atom = AtomicClaim(
        id="native_literal",
        parameter_id=parameter,
        label=label,
        kind="condition",
        operator="text",
        text_value=literal,
        raw_text=literal,
        source_pages=[1],
    )
    row = source(literal, [atom])
    before = row.model_dump(mode="json")
    result = normalize_requirement(row, native_first=True)
    converted = result.claims[0]
    assert converted.parameter_id == parameter
    assert converted.operator == operator
    assert converted.unit == unit
    assert converted.normalization_state != "unresolved"
    assert converted.value == (Decimal(value) if value else None)
    assert converted.minimum == (Decimal(minimum) if minimum else None)
    assert converted.maximum == (Decimal(maximum) if maximum else None)
    assert converted.text_value == literal
    assert any(
        a.get("rule") == "source_proved_numeric_text_literal"
        for a in result.normalization_actions
    )
    assert row.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "parameter,literal,quote,unit",
    [
        ("temperature", "60°", "60°", None),
        ("measurement_angle", "60° except the north side", "60° except the north side", None),
        ("measurement_angle", "60°", "90°", None),
        ("measurement_angle", "60°", "60°", "°C"),
    ],
)
def test_numeric_text_repair_rejects_conflicting_units_missing_evidence_and_extra_qualifiers(
    parameter, literal, quote, unit
):
    atom = AtomicClaim(
        id="native_literal",
        parameter_id=parameter,
        label=parameter.replace("_", " "),
        kind="condition",
        operator="text",
        text_value=literal,
        raw_text=quote,
        unit=unit,
        source_pages=[1],
    )
    result = normalize_requirement(source(quote, [atom]), native_first=True)
    assert not any(
        a.get("rule") == "source_proved_numeric_text_literal"
        for a in result.normalization_actions
    )


def test_omitted_numeric_condition_requests_source_review_without_a_guessed_demand():
    row = source("23 °C for 2 h", [temperature()])
    projected = normalize_requirement(row, native_first=True)
    assert not any(c.parameter_id == "duration" for c in projected.claims)
    assert any(c.id == "req_native_view_claim_1" for c in projected.claims)
    assert any("2 h" in issue for issue in projected.review_issues)
    audit = next(a for a in projected.normalization_actions if a.get("requires_source_review"))
    assert audit["source_clause"] == "23 °C for 2 h"
    assert audit["comparison_demand_created"] is False


def test_unrepresented_material_and_negation_remain_visible():
    for qualifier in ("in nitrogen atmosphere", "except aluminium", "with QZ-19 fixture"):
        row = source(f"23 °C {qualifier}", [temperature()])
        projected = normalize_requirement(row, native_first=True)
        assert len(projected.claims) == 1
        assert projected.review_issues
        assert any(
            qualifier.casefold() in a.get("source_clause", "").casefold()
            and a["requires_source_review"]
            for a in projected.normalization_actions
        )


def test_native_workflow_text_is_not_reintroduced_as_a_criterion():
    raw = "If the specimen cracks, retain its photographs for engineer review."
    workflow = AtomicClaim(
        id="native_workflow",
        parameter_id="review_instruction",
        label="Engineer review instruction",
        kind="instruction",
        operator="text",
        text_value=raw,
        raw_text=raw,
        source_pages=[1],
    )
    row = source(raw, [workflow], criteria=True)
    legacy = normalize_requirement(row)
    projected = normalize_requirement(row, native_first=True)
    assert len(legacy.claims) == 2
    assert len(projected.claims) == 1
    assert projected.claims[0] == legacy.claims[0]
    note = next(a for a in projected.normalization_actions if "source_clause" in a)
    assert note["source_clause"] == raw
    assert note["source_pages"] == [1]
    assert note["represented_by"] == [projected.claims[0].id]


def test_invalid_native_value_stays_unresolved_and_true_quantity_is_a_source_audit():
    row = source("23 °C", [temperature(value=Decimal("999"))])
    projected = normalize_requirement(row, native_first=True)
    assert projected.claims[0].normalization_state == "unresolved"
    assert projected.claims[0].value == 999
    assert len(projected.claims) == 1
    assert any("23" in issue for issue in projected.review_issues)
    assert any(
        p["parameter_id"] == "temperature" and p["value"] == "23"
        for a in projected.normalization_actions
        for p in a.get("parser_proposals", [])
    )


def test_native_numeric_fact_does_not_cover_another_kind_of_quantity():
    row = source("23 °C and a thickness of 23 µm", [temperature()])
    projected = normalize_requirement(row, native_first=True)
    assert len(projected.claims) == 1
    assert any("µm" in issue for issue in projected.review_issues)


def test_explicit_initial_and_after_cycle_facts_are_not_merged():
    raw = "Initial conditioning at 23 °C; after cycling at 40 °C."
    row = source(raw, [temperature("Initial conditioning at 23 °C", stage="initial")])
    projected = normalize_requirement(row, native_first=True)
    assert len(projected.claims) == 1
    assert any("40" in issue for issue in projected.review_issues)
    assert any(
        "after cycling" in a.get("source_clause", "")
        and a["requires_source_review"]
        for a in projected.normalization_actions
    )


def test_native_discrete_criterion_label_does_not_create_a_second_numeric_fact():
    raw = "Acceptance criterion: adhesion class 0 or 1."
    claim = AtomicClaim(
        id="native_class",
        parameter_id="adhesion_class",
        label="Adhesion class",
        kind="criterion",
        operator="one_of",
        values=[Decimal(0), Decimal(1)],
        text_value="adhesion class 0 or 1",
        raw_text=raw,
        source_pages=[1],
    )
    row = source(raw, [claim], criteria=True)
    projected = normalize_requirement(row, native_first=True)
    assert len(projected.claims) == 1
    assert projected.claims[0].text_value == claim.text_value


def test_registered_numeric_parameter_does_not_leave_a_label_only_qualifier():
    raw = "Apply a load of 750 g."
    claim = temperature(
        raw,
        parameter_id="load",
        label="Load",
        value=Decimal(750),
        unit="g",
    )
    projected = normalize_requirement(source(raw, [claim]), native_first=True)
    assert len(projected.claims) == 1


def test_explicit_missing_customer_limit_is_information_not_missing_lab_capability():
    raw = "No specimen acceptance limit is specified in this document."
    normalized = normalize_requirement(source(raw))
    result = compare_requirement(normalized, [], method_status="supported")
    assert result.checks[-1].status == "informational"
    assert result.status == "fully_matched"
    extra = source(raw + " Temperature must stay below 50 °C.")
    result = compare_requirement(normalize_requirement(extra), [], method_status="supported")
    assert any(c.status == "unknown" and c.parameter_id == "temperature" for c in result.checks)


def test_complete_native_spelled_unit_condition_has_no_parser_word_fragments():
    raw = "During exposure, use water at 60 degrees Celsius."
    claim = temperature(
        raw,
        parameter_id="water_temperature",
        label="Water temperature",
        value=Decimal(60),
        stage="exposure",
    )
    projected = normalize_requirement(source(raw, [claim]), native_first=True)
    assert len(projected.claims) == 1
    assert projected.claims[0].parameter_id == "water_temperature"
    restricted = source(raw.rstrip(".") + " with QZ-19 fixture.", [claim])
    checked = normalize_requirement(restricted, native_first=True)
    assert len(checked.claims) == 1
    assert any("qz-19" in issue.casefold() for issue in checked.review_issues)


def test_coarse_summary_role_does_not_duplicate_native_criterion():
    raw = "The separate topcoat shall be between 42 and 55 µm."
    claim = temperature(
        raw, parameter_id="topcoat_thickness", label="Topcoat thickness",
        kind="criterion", stage="measurement", operator="range", value=None,
        minimum=Decimal(42), maximum=Decimal(55), unit="µm",
    )
    projected = normalize_requirement(source(raw, [claim]), native_first=True)
    assert len(projected.claims) == 1
    assert projected.claims[0].kind == "criterion"
    assert not projected.review_issues


def test_unrecognized_dimensional_source_quantity_is_a_visible_omission():
    row = source("23 °C using panels 100 × 200 mm.", [temperature()])
    projected = normalize_requirement(row, native_first=True)
    assert len(projected.claims) == 1
    assert any("200 mm" in issue for issue in projected.review_issues)


def test_custom_parameter_survives_with_grounded_value_and_evidence():
    raw = "Use the violet-tipped fixture."
    claim = AtomicClaim(
        id="new_fixture", parameter_id="fixture_tip_colour", label="Fixture tip colour",
        kind="condition", operator="text", text_value="violet-tipped fixture",
        raw_text=raw, source_pages=[1],
    )
    projected = normalize_requirement(source(raw, [claim]), native_first=True)
    assert len(projected.claims) == 1
    assert projected.claims[0].parameter_id == "fixture_tip_colour"
    assert projected.claims[0].normalization_state == "parsed"
    assert not projected.review_issues


def test_meaningful_native_numeric_label_names_a_custom_field_without_a_registry():
    raw = "Core diameter shall be 100 mm."
    claim = temperature(
        raw, parameter_id=None, label="Core diameter", value=Decimal(100), unit="mm",
        normalization_state="unresolved",
    )
    projected = normalize_requirement(source(raw, [claim]), native_first=True)
    assert projected.claims[0].parameter_id == "custom_core_diameter"
    assert projected.claims[0].normalization_state == "parsed"
    assert projected.claims[0].raw_text == raw


def test_generic_label_can_name_only_a_grounded_local_numeric_fact():
    raw = "Core diameter shall be 100 mm."
    claim = temperature(
        raw, parameter_id=None, label="Unstructured Requirement", value=Decimal(100),
        unit="mm", normalization_state="unresolved",
    )
    first = normalize_requirement(source(raw, [claim]), native_first=True)
    second = normalize_requirement(source(raw, [claim]), native_first=True)
    retained = first.claims[0]
    assert retained.parameter_id.startswith("source_quantity_")
    assert retained.parameter_id == second.claims[0].parameter_id
    assert retained.normalization_state == "parsed"
    assert retained.label == "Source quantity"
    assert claim.parameter_id is None and claim.normalization_state == "unresolved"
    audit = next(
        a for a in first.normalization_actions if a.get("rule") == "grounded_native_local_quantity"
    )
    assert audit["scientific_identity_inferred"] is False
    assert audit["semantic_identity_requires_alignment"] is True


def test_local_quantity_name_cannot_certify_a_wrong_value_unit_or_operator():
    raw = "The core diameter must be at least 100 mm."
    for changes in (
        {"value": Decimal(999), "operator": ">="},
        {"unit": "s", "operator": ">="},
        {"operator": "<="},
    ):
        claim = temperature(
            raw, parameter_id=None, label="Unknown parameter", value=Decimal(100),
            unit="mm", normalization_state="unresolved",
        ).model_copy(update=changes)
        normalized = normalize_requirement(source(raw, [claim]), native_first=True)
        assert normalized.claims[0].parameter_id is None
        assert normalized.claims[0].normalization_state == "unresolved"
        assert not any(
            a.get("rule") == "grounded_native_local_quantity"
            for a in normalized.normalization_actions
        )


def test_unidentified_fragment_is_not_promoted_to_a_local_source_fact():
    raw = "coat"
    claim = AtomicClaim(
        id="unidentified_prose", parameter_id=None, label="Unstructured Requirement",
        kind="condition", operator="text", text_value=raw, raw_text=raw,
        source_pages=[1], normalization_state="unresolved",
    )
    normalized = normalize_requirement(source(raw, [claim]), native_first=True)
    assert normalized.claims[0].parameter_id is None
    assert normalized.claims[0].normalization_state == "unresolved"


def test_native_verbatim_protocol_can_keep_a_neutral_source_fact_identity():
    raw = "Record each specimen result separately."
    claim = AtomicClaim(
        id="native_protocol", parameter_id=None, label="Unstructured Requirement",
        kind="reporting", operator="required", text_value=raw, raw_text=raw,
        source_pages=[1], normalization_state="unresolved",
    )
    normalized = normalize_requirement(source(raw, [claim]), native_first=True)
    assert normalized.claims[0].parameter_id.startswith("source_fact_")
    assert normalized.claims[0].normalization_state == "parsed"
    assert normalized.claims[0].raw_text == raw
    audit = next(a for a in normalized.normalization_actions
                 if a.get("rule") == "grounded_native_local_fact")
    assert audit["scientific_identity_inferred"] is False
    assert audit["semantic_identity_requires_alignment"] is True


def test_neutral_source_fact_needs_literal_value_and_its_owned_page():
    raw = "Record each specimen result separately."
    original = AtomicClaim(
        id="native_protocol", parameter_id=None, label="Unstructured Requirement",
        kind="reporting", operator="required", text_value=raw, raw_text=raw,
        source_pages=[1], normalization_state="unresolved",
    )
    for updates in (
        {"text_value": "Combine every specimen into one average."},
        {"source_pages": [2]},
        {"raw_text": "coat", "text_value": "coat"},
    ):
        claim = original.model_copy(update=updates)
        normalized = normalize_requirement(source(raw, [claim]), native_first=True)
        assert normalized.claims[0].parameter_id is None
        assert normalized.claims[0].normalization_state == "unresolved"


def test_legacy_parser_prose_does_not_receive_a_native_source_fact_name():
    normalized = normalize_requirement(
        source("Record each specimen result separately."), native_first=True
    )
    assert not any((claim.parameter_id or "").startswith("source_fact_")
                   for claim in normalized.claims)
