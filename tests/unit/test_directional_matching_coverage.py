"""Semantic interpretation cannot bypass scientific numeric/ownership checks."""

from copy import deepcopy

import pytest

from scione.matching.coverage import coverage_pair
from scione.matching.models import ClaimAlignment
from scione.matching.semantics import SemanticEquivalenceError
from scione.specifications.models import CustomerRequirement, RequirementEvidence
from scione.structured.comparison import compare_requirement
from scione.structured.models import AtomicClaim, NormalizedRequirement


def numeric_pair(value=25, *, parameter="temperature", unit="°C"):
    quote = f"During measurement, set {parameter} to {value} {unit}."
    request = AtomicClaim(
        id="source",
        parameter_id=parameter,
        label=parameter,
        kind="condition",
        stage="measurement",
        operator="=",
        value=value,
        unit=unit,
        raw_text=quote,
        source_context=quote,
        source_pages=[1],
    )
    lab = AtomicClaim(
        id="lab",
        parameter_id=parameter,
        label=parameter,
        kind="condition",
        stage="measurement",
        operator="range",
        minimum=20,
        maximum=70,
        unit=unit,
        raw_text=f"Lab operating range20–70 {unit}",
    )
    source = CustomerRequirement(
        id="req_test",
        original_test_name="Synthetic test",
        atomic_claims=[request],
        evidence=[RequirementEvidence(page=1, quote=quote)],
    )
    link = ClaimAlignment(
        requirement_claim_id="source",
        capability_claim_ids=["lab"],
        relation="same_parameter",
        scope_relation="equivalent",
        reason="The source context repeats the same measured quantity and operating phase.",
    )
    return request, lab, source, link


@pytest.mark.parametrize("value,expected", [(25, "supported"), (70, "supported"), (75, "gap")])
def test_ai_can_align_prose_but_code_decides_containment(value, expected):
    request, lab, source, link = numeric_pair(value)
    before = deepcopy((request.model_dump(), lab.model_dump()))
    projected, actions = coverage_pair(request, lab, link, source=source)
    result = compare_requirement(
        NormalizedRequirement(requirement_id="req_test", test_name="T", claims=[request]),
        [projected],
        method_status="supported",
    )
    assert result.checks[-1].status == expected
    assert actions[0]["semantic_inference"] is True
    assert (request.model_dump(), lab.model_dump()) == before


@pytest.mark.parametrize(
    "change",
    [
        {"stage": "conditioning"},
        {"stage": "exposure"},
        {"sequence": 2, "configuration": "fixture-B"},
        {"availability": "unknown"},
    ],
)
def test_semantic_scope_does_not_certify_incompatible_or_unknown_lab_declarations(change):
    request, lab, source, link = numeric_pair()
    request = request.model_copy(update={"sequence": 1, "configuration": "fixture-A"})
    with pytest.raises(SemanticEquivalenceError):
        coverage_pair(request, lab.model_copy(update=change), link, source=source)


def test_numeric_values_cannot_be_replaced_by_semantic_text_verdict():
    request, lab, source, link = numeric_pair()
    link.value_relation = "covered"
    with pytest.raises(SemanticEquivalenceError, match="numeric"):
        coverage_pair(request, lab, link, source=source)


def test_unlocated_source_cannot_receive_semantic_coverage():
    request, lab, source, link = numeric_pair()
    source.evidence = []
    with pytest.raises(SemanticEquivalenceError, match="grounded"):
        coverage_pair(request, lab, link, source=source)


def test_multilingual_custom_text_can_use_evidence_bound_coverage():
    request, lab, source, link = numeric_pair()
    request = AtomicClaim(
        id="source",
        parameter_id="individual_readings",
        label="结果报告",
        kind="reporting",
        operator="required",
        text_value="逐点提供结果",
        raw_text="逐点提供结果",
        source_pages=[1],
    )
    lab = AtomicClaim(
        id="lab",
        parameter_id="individual_readings",
        label="Reporting service",
        kind="reporting",
        operator="required",
        text_value="Individual readings at every location",
        raw_text="Individual readings at every location",
    )
    source.evidence = [RequirementEvidence(page=1, quote=request.raw_text)]
    link.scope_relation = None
    link.value_relation = "equivalent"
    projected, actions = coverage_pair(request, lab, link, source=source)
    result = compare_requirement(
        NormalizedRequirement(requirement_id="req_test", test_name="T", claims=[request]),
        [projected],
        method_status="supported",
    )
    assert result.checks[-1].status == "supported"
    assert lab.text_value != projected.text_value
    assert actions[0]["rule"] == "semantic_directional_value"


def test_text_coverage_cannot_rename_resource_identifiers():
    request, lab, source, link = numeric_pair()
    request = AtomicClaim(
        id="source",
        parameter_id="fixture",
        label="Fixture",
        kind="condition",
        operator="required",
        text_value="Fixture XQ-11",
        raw_text="Fixture XQ-11",
        source_pages=[1],
    )
    lab = request.model_copy(update={"id": "lab", "text_value": "Fixture XQ-12"})
    source.evidence = [RequirementEvidence(page=1, quote=request.raw_text)]
    link.scope_relation = None
    link.value_relation = "covered"
    with pytest.raises(SemanticEquivalenceError, match="identifier"):
        coverage_pair(request, lab, link, source=source)
