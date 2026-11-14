"""Subdimensions, layers and code identities need their own source evidence."""

from decimal import Decimal

import pytest

from scione.specifications.models import CustomerRequirement
from scione.structured.models import AtomicClaim
from scione.structured.normalization import known_source_fact_problem, normalize_requirement


def normalize(claim, *, evidence=None):
    source = CustomerRequirement(
        id="req_science",
        original_test_name="Customer test",
        evidence=[{"page": 1, "quote": evidence or claim.raw_text}],
        atomic_claims=[claim],
    )
    result = normalize_requirement(source)
    return result.claims[0], result.review_issues


@pytest.mark.parametrize(
    "raw,context,value,tolerance",
    [
        ("nominally 3.2 ± 0.2 mm thick", "nominal specimen thickness", "3.2", ".2"),
        ("试验基材为厚度 1.2 ± 0.1 mm 的汽车外饰用铝合金板", "基材厚度", "1.2", ".1"),
    ],
)
def test_length_identity_cannot_certify_source_stated_thickness(raw, context, value, tolerance):
    source = AtomicClaim(
        id="source",
        parameter_id="specimen_length",
        label="Specimen length",
        operator="=",
        value=value,
        tolerance=tolerance,
        unit="mm",
        raw_text=raw,
        source_context=context,
        source_pages=[1],
    )
    result, issues = normalize(source)
    assert result.normalization_state == "unresolved"
    assert result.parameter_id is None
    assert any("thickness" in issue for issue in issues)
    assert source.parameter_id == "specimen_length"
    assert result.value == Decimal(value)


def test_unstated_topcoat_specificity_cannot_replace_total_edge_coating():
    source = AtomicClaim(
        id="source",
        parameter_id="topcoat_thickness",
        label="Topcoat Thickness",
        kind="criterion",
        operator=">=",
        value=125,
        unit="µm",
        raw_text="六个测点中的最低值不得低于 125 µm",
        source_context="折弯边缘六个测点中的最低值",
        source_pages=[1],
    )
    result, issues = normalize(
        source,
        evidence=(
            "外壳折弯边缘应单独测厚，距弯曲外缘 5–10 mm 处各取三点；"
            "六个测点中的最低值不得低于 125 µm。"
        ),
    )
    assert result.parameter_id is None
    assert result.normalization_state == "unresolved"
    assert any("Topcoat/layer specificity" in issue for issue in issues)
    assert result.value == 125


def test_immediate_latter_antecedent_can_prove_topcoat_without_model_label():
    raw = "between 42 and 55 µm on every panel"
    source = AtomicClaim(
        id="source",
        parameter_id="topcoat_thickness",
        label="Topcoat thickness",
        kind="criterion",
        operator="range",
        minimum=42,
        maximum=55,
        unit="µm",
        raw_text=raw,
        source_pages=[1],
    )
    result, _issues = normalize(
        source,
        evidence=(
            "Record total dry film build and the separate topcoat thickness; "
            "the latter must be between 42 and 55 µm on every panel."
        ),
    )
    assert result.normalization_state == "parsed"
    assert result.parameter_id == "topcoat_thickness"


@pytest.mark.parametrize(
    "value,raw,valid",
    [
        ("D65", "Compare under D65 lighting.", True),
        ("D50", "Compare under D65 lighting.", False),
        ("D65", "Compare under D650 lighting.", False),
        ("D５０", "Compare under D65 lighting.", False),
        ("D６５", "Compare under D65 lighting.", True),
        ("XQ-a", "Use fixture XQ-A.", False),
        ("XQ-a", "Use fixture XQ-a.", True),
        ("D50", "Use D65, not D50.", False),
        ("紫外灯", "Use an ultraviolet lamp.", True),
    ],
)
def test_code_like_text_values_require_exact_source_identity(value, raw, valid):
    source = AtomicClaim(
        id="source",
        parameter_id="requested_resource",
        label="Requested resource",
        operator="text",
        text_value=value,
        raw_text=raw,
        source_pages=[1],
    )
    result, issues = normalize(source)
    assert (result.normalization_state == "parsed") is valid
    assert result.text_value == value
    if not valid:
        assert any("code-like" in issue for issue in issues)


def test_strict_length_boundary_conflicting_with_rejection_clause_is_unresolved():
    raw = "不允许出现连续长度大于 1.0 mm 的露铝区域"
    source = AtomicClaim(
        id="source",
        parameter_id="exposed_aluminum_length",
        label="Exposed aluminum length",
        kind="criterion",
        operator="<",
        value=1,
        unit="mm",
        raw_text=raw,
        source_context="连续长度",
        source_pages=[1],
    )
    result, _issues = normalize(source)
    assert result.normalization_state == "unresolved"
    assert result.operator == "<"


@pytest.mark.parametrize(
    "parameter,unit",
    [
        ("impact_energy", "kJ/m²"),
        ("temperature", "°C/min"),
        ("pressure", "MPa/s"),
    ],
)
def test_known_rate_dimension_mismatch_has_an_actionable_source_reason(parameter, unit):
    source = AtomicClaim(
        id="source",
        parameter_id=parameter,
        label=parameter,
        operator="=",
        value=2,
        unit=unit,
        raw_text=f"Recorded condition: 2 {unit}",
        source_pages=[1],
    )
    requirement = CustomerRequirement(
        id="req_dimension",
        original_test_name="Customer test",
        evidence=[{"page": 1, "quote": source.raw_text}],
        atomic_claims=[source],
    )
    assert "per-unit/rate" in known_source_fact_problem(source, requirement)


def test_unknown_count_units_are_not_labelled_a_known_scientific_conflict():
    source = AtomicClaim(
        id="source",
        parameter_id="specimen_count",
        label="Specimen count",
        operator="=",
        value=2,
        unit="lots",
        raw_text="two lots",
        source_pages=[1],
    )
    requirement = CustomerRequirement(
        id="req_counts",
        original_test_name="Customer test",
        evidence=[{"page": 1, "quote": source.raw_text}],
        atomic_claims=[source],
    )
    assert known_source_fact_problem(source, requirement) is None


def test_no_specimen_may_exceed_frame_allows_equality_but_not_a_stricter_model_bound():
    raw = "No specimen may have an edge curl standing more than 3 mm above a level plate."
    source = AtomicClaim(
        id="source",
        parameter_id="edge_curl_height",
        label="Edge curl height",
        kind="criterion",
        operator="<=",
        value=3,
        unit="mm",
        raw_text=raw,
        source_pages=[1],
    )
    correct, _ = normalize(source)
    assert correct.normalization_state == "parsed"
    too_strict, _ = normalize(source.model_copy(update={"operator": "<"}))
    assert too_strict.normalization_state == "unresolved"
