"""Operating capacity comparisons must not turn uncertainty into material passes."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from scione.catalogs.models import LabCapability, LabOperatingLimit
from scione.reports.capability import assess_capability
from scione.specifications.models import CustomerRequirement


def requirement(*conditions: str, **updates: object) -> CustomerRequirement:
    values = {
        "id": "req_capacity",
        "original_test_name": "Fictional operating test",
        "conditions": [{"name": "Customer condition", "raw_value": item} for item in conditions],
        "evidence": [{"page": 1, "quote": "Fictional customer operating request."}],
        **updates,
    }
    return CustomerRequirement.model_validate(values)


def lab(*limits: dict[str, object], **updates: object) -> LabCapability:
    return LabCapability.model_validate(
        {
            "id": "cap_capacity",
            "canonical_test_id": "pencil_hardness",
            "method_ids": ["iso_15184"],
            "structured_limits": list(limits),
            **updates,
        }
    )


def numeric(parameter: str, minimum: str, maximum: str, unit: str) -> dict[str, object]:
    return {"parameter": parameter, "minimum": minimum, "maximum": maximum, "unit": unit}


@pytest.mark.parametrize(
    ("text", "limit"),
    [
        ("750 g", numeric("load", "750", "750", "g")),
        ("0.75 kg test load", numeric("load", "750", "750", "g")),
        ("2 mm spacing", numeric("spacing", "1", "3", "mm")),
        ("23 °C", numeric("temperature", "0", "40", "°C")),
        ("−20 °C", numeric("temperature", "-30", "30", "°C")),
        ("20 to 30 °C", numeric("temperature", "0", "40", "°C")),
        ("30 min", numeric("duration", "0.5", "0.5", "h")),
        ("3600 s", numeric("duration", "1", "1", "h")),
        ("6 cycles", numeric("cycles", "1", "10", "cycles")),
        ("2 bar", numeric("pressure", "1", "3", "bar")),
        ("60°", {"parameter": "angle", "values": ["20", "60", "85"], "unit": "°"}),
    ],
)
def test_explicit_operating_values_with_unit_conversion_are_supported(
    text: str, limit: dict[str, object]
) -> None:
    result = assess_capability(requirement(text), lab(limit))
    assert result.status == "supported"
    assert all(check.status == "supported" for check in result.checks)


def test_all_stated_conditions_must_be_covered_not_only_first_number() -> None:
    capability = lab(numeric("load", "750", "750", "g"))
    result = assess_capability(requirement("750 g", "2 mm spacing"), capability)
    assert result.status == "unknown"
    assert [check.status for check in result.checks] == ["supported", "unknown"]


@pytest.mark.parametrize("name", ["Spacing", "spacing", "SPACING", "间距"])
def test_spacing_parameter_label_is_case_insensitive_and_out_of_range_stays_a_gap(
    name: str,
) -> None:
    request = requirement(conditions=[{"name": name, "raw_value": "3", "unit": "mm"}])
    result = assess_capability(request, lab(numeric("spacing", "2", "2", "mm")))
    assert result.status == "gap"


@pytest.mark.parametrize("name", ["Dolly diameter", "DOLLY DIAMETER"])
def test_other_lengths_are_not_treated_as_spacing_from_the_parameter_label(name: str) -> None:
    request = requirement(conditions=[{"name": name, "raw_value": "3", "unit": "mm"}])
    result = assess_capability(request, lab(numeric("spacing", "2", "2", "mm")))
    assert result.status == "unknown"


@pytest.mark.parametrize("text", ["at -20 °C", "23 °C and after conditioning at -20 °C"])
def test_temperature_below_lab_range_is_a_known_gap(text: str) -> None:
    result = assess_capability(
        requirement(text),
        lab(numeric("temperature", "23", "23", "°C"), declared_constraints="23 °C only."),
    )
    assert result.status == "gap"
    assert any(check.status == "gap" for check in result.checks)


def test_known_temperature_gap_survives_other_unparsed_grit_and_repetition_clauses() -> None:
    result = assess_capability(
        requirement(
            "chilled iron grit 4 to 5 mm, 2 x 500 g at 2 bar; "
            "at 23 °C and after conditioning at -20 °C"
        ),
        lab(numeric("temperature", "23", "23", "°C")),
    )
    assert result.status == "gap"
    assert any(check.label == "Temperature" and check.status == "gap" for check in result.checks)
    assert any(check.status == "unknown" for check in result.checks)
    assert not any(check.label == "Load" for check in result.checks)


@pytest.mark.parametrize(
    "text",
    [
        "750 g, all substrates",
        "750 g except plastic",
        "750 g ± 5 g",
        "750 g for special client preparation",
        "minimum 750 g",
        "20, 60 and 85°",
        "2 x 500 g at 2 bar",
        "20 mm dolly",
        "chilled iron grit 4 to 5 mm",
    ],
)
def test_unknown_qualifiers_and_ambiguous_numeric_roles_are_not_erased(text: str) -> None:
    capability = lab(
        numeric("load", "0", "1000", "g"),
        numeric("spacing", "0", "100", "mm"),
        numeric("angle", "0", "100", "°"),
        numeric("pressure", "0", "10", "bar"),
    )
    result = assess_capability(requirement(text), capability)
    assert result.status == "unknown"
    assert any(check.status == "unknown" for check in result.checks)


def test_exact_qualitative_declaration_can_cover_an_operating_clause() -> None:
    result = assess_capability(
        requirement("750 g; all bake conditions, all substrates"),
        lab(
            {
                "parameter": "condition",
                "supported_values": ["750 g; all bake conditions, all substrates"],
            }
        ),
    )
    assert result.status == "supported"


def test_qualitative_declaration_does_not_apply_to_a_different_condition() -> None:
    result = assess_capability(
        requirement("all substrates"),
        lab({"parameter": "condition", "supported_values": ["coated metal substrates"]}),
    )
    assert result.status == "unknown"


def test_material_acceptance_criteria_are_not_treated_as_a_capacity_gap_or_pass() -> None:
    result = assess_capability(
        requirement(
            "750 g",
            acceptance_criteria=[{"name": "Acceptance", "raw_value": "≥ F; no cracking"}],
        ),
        lab(numeric("load", "750", "750", "g")),
    )
    assert result.status == "supported"
    check = result.checks[-1]
    assert check.status == "informational"
    assert "not predicted" in check.declared
    assert "≥ F" in check.requested


def test_acceptance_criteria_alone_do_not_establish_operating_support() -> None:
    result = assess_capability(
        requirement(acceptance_criteria=[{"name": "Acceptance", "raw_value": "≥ F"}]),
        lab(numeric("load", "750", "750", "g")),
    )
    assert result.status == "unknown"


def test_bare_source_section_is_not_confused_with_customer_operating_scope() -> None:
    result = assess_capability(
        requirement("750 g", applicability=["Section 3.2"]),
        lab(numeric("load", "750", "750", "g")),
    )
    assert result.status == "supported"


@pytest.mark.parametrize(
    "heading",
    [
        "3.2 Mechanical",
        "3.1 Appearance",
        "4.1 Humidity, corrosion and thermal",
        "4.2 Weathering",
        "4.3 Chemical and fluid resistance",
        "5. Repair and compatibility",
    ],
)
def test_known_source_headings_are_locators_not_operating_constraints(heading: str) -> None:
    result = assess_capability(
        requirement("750 g", applicability=[heading]),
        lab(numeric("load", "750", "750", "g")),
    )
    assert result.status == "supported"


@pytest.mark.parametrize(
    "scope", ["3.2 only aluminium", "3.2 Mechanical, aluminium only", "4.2 special process"]
)
def test_number_prefixed_real_scope_is_not_discarded(scope: str) -> None:
    result = assess_capability(
        requirement("750 g", applicability=[scope]),
        lab(numeric("load", "750", "750", "g")),
    )
    assert result.status == "unknown"


@pytest.mark.parametrize("field", ["applicability", "special_instructions"])
def test_real_scope_and_special_instructions_require_explicit_declarations(field: str) -> None:
    result = assess_capability(
        requirement("750 g", **{field: ["All substrates and bake conditions"]}),
        lab(numeric("load", "750", "750", "g")),
    )
    assert result.status == "unknown"


def test_exact_structured_scope_and_instruction_can_be_compared() -> None:
    result = assess_capability(
        requirement(
            "750 g",
            applicability=["Coated flat panels"],
            special_instructions=["Report individual results"],
        ),
        lab(
            numeric("load", "750", "750", "g"),
            {"parameter": "scope", "supported_values": ["Coated flat panels"]},
            {"parameter": "instruction", "supported_values": ["Report individual results"]},
        ),
    )
    assert result.status == "supported"


@pytest.mark.parametrize(
    "constraints",
    [
        "Bake coverage to confirm.",
        "Metal panels only.",
        "Special conditioning is not declared.",
    ],
)
def test_existing_unresolved_lab_restriction_is_not_hidden_by_new_numeric_fields(
    constraints: str,
) -> None:
    result = assess_capability(
        requirement("750 g"),
        lab(numeric("load", "750", "750", "g"), declared_constraints=constraints),
    )
    assert result.status == "unknown"


def test_conflicting_typed_and_existing_text_need_human_clarification() -> None:
    result = assess_capability(
        requirement("750 g"),
        lab(numeric("load", "750", "750", "g"), declared_constraints="500 g test load."),
    )
    assert result.status == "unknown"
    assert "conflict" in result.reasons[0]


@pytest.mark.parametrize(
    "notes", ["Metal panels only.", "Low-temperature testing not declared.", "750 g only."]
)
def test_operational_restrictions_in_notes_are_not_ignored(notes: str) -> None:
    result = assess_capability(
        requirement("500 g"),
        lab(numeric("load", "0", "1000", "g"), notes=notes),
    )
    assert result.status == "unknown"


def test_harmless_synthetic_price_note_is_not_an_operating_restriction() -> None:
    result = assess_capability(
        requirement("750 g"),
        lab(
            numeric("load", "750", "750", "g"), notes="Synthetic price; actual price not supplied."
        ),
    )
    assert result.status == "supported"


def test_broader_typed_range_cannot_override_old_temperature_only_restriction() -> None:
    result = assess_capability(
        requirement("-20 °C"),
        lab(numeric("temperature", "-30", "50", "°C"), declared_constraints="23 °C only."),
    )
    assert result.status == "unknown"
    assert "conflict" in result.reasons[0]


def test_old_unstructured_declaration_remains_backward_compatible_and_unknown() -> None:
    old = lab(declared_constraints="750 g test load.")
    assert old.structured_limits == []
    assert assess_capability(requirement("750 g"), old).status == "unknown"


@pytest.mark.parametrize(
    "limit",
    [
        {"parameter": "load", "minimum": "1", "maximum": "0", "unit": "g"},
        {"parameter": "load", "minimum": "-1", "unit": "g"},
        {"parameter": "temperature", "minimum": "1", "unit": "°F"},
        {"parameter": "duration", "unit": "h"},
        {"parameter": "load", "values": ["Infinity"], "unit": "g"},
        {"parameter": "angle", "values": ["20"], "minimum": "0", "unit": "°"},
        {"parameter": "scope", "supported_values": [""]},
        {"parameter": "scope", "minimum": "0", "supported_values": ["panels"]},
    ],
)
def test_inconsistent_structured_declarations_are_rejected(limit: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LabOperatingLimit.model_validate(limit)


def test_range_precision_and_original_requirement_are_preserved() -> None:
    request = requirement("750 g")
    before = request.model_dump_json()
    capability = lab(numeric("load", "749.999999999999999999", "750.000000000000000001", "g"))
    assert assess_capability(request, capability).status == "supported"
    assert capability.structured_limits[0].minimum == Decimal("749.999999999999999999")
    assert request.model_dump_json() == before
