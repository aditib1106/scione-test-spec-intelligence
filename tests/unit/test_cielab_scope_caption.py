"""Metric captions cannot masquerade as equipment, or erase genuine setup constraints."""

from copy import deepcopy

import pytest

from scione.matching.coverage import coverage_pair
from scione.matching.models import ClaimAlignment
from scione.matching.semantics import SemanticEquivalenceError
from scione.specifications.models import CustomerRequirement, RequirementEvidence
from scione.structured.comparison import compare_requirement
from scione.structured.models import AtomicClaim, NormalizedRequirement


def pair():
    quote = "The colour difference calculated from measured L*, a* and b* may not exceed 1.0."
    requested = AtomicClaim(
        id="source", parameter_id="color_difference", label="Colour difference",
        kind="criterion", stage="measurement", operator="<=", value="1.0", unit="ΔE*ab",
        raw_text=quote, source_pages=[1], source_context="Overall master-panel colour difference",
    )
    declared = AtomicClaim(
        id="lab", parameter_id="color_difference", label="Reportable colour difference",
        kind="reporting", stage="measurement", operator="range", minimum=0, maximum=10,
        unit="ΔE*ab", raw_text="Report colour difference from 0 to 10 delta-E.",
        source_context="CIE L*, a*, b* comparison on OEM-coated steel against customer master",
    )
    source = CustomerRequirement(
        id="req_colour", original_test_name="Colour difference", atomic_claims=[requested],
        evidence=[RequirementEvidence(page=1, quote=quote)],
    )
    alignment = ClaimAlignment(
        requirement_claim_id="source", capability_claim_ids=["lab"], relation="same_parameter",
        scope_relation="covered", reason="Both describe master-panel CIELAB colour difference.",
    )
    return requested, declared, source, alignment


def test_explicit_same_cielab_metric_caption_does_not_require_extra_setup_identity():
    requested, declared, source, alignment = pair()
    before = deepcopy((requested.model_dump(), declared.model_dump()))
    projected, actions = coverage_pair(requested, declared, alignment, source=source)
    compared = compare_requirement(
        NormalizedRequirement(requirement_id="req_colour", test_name="Colour", claims=[requested]),
        [projected], method_status="supported",
    )
    assert compared.checks[-1].status == "supported"
    assert any(action["rule"] == "explicit_cielab_metric_caption" for action in actions)
    assert (requested.model_dump(), declared.model_dump()) == before


@pytest.mark.parametrize("context", [
    "CIE L*, a*, b* using fixture XQ-9",
    "CIE 2000 comparison",
    "CIE L*, a*, b* compared using CIEDE2000",
    "CIE comparison without a specified formula",
])
def test_caption_does_not_erase_other_setup_codes_or_formula_qualifiers(context):
    requested, declared, source, alignment = pair()
    declared.source_context = context
    with pytest.raises(SemanticEquivalenceError, match="setup identifier"):
        coverage_pair(requested, declared, alignment, source=source)


@pytest.mark.parametrize("change", [
    {"unit": "ΔE00"},
    {"parameter_id": "temperature", "unit": "°C"},
    {"parameter_id": "custom_optical_metric"},
])
def test_cie_caption_exception_requires_the_exact_known_metric_and_same_unit(change):
    requested, declared, source, alignment = pair()
    declared = declared.model_copy(update=change)
    with pytest.raises(SemanticEquivalenceError):
        coverage_pair(requested, declared, alignment, source=source)
