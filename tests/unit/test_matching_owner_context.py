"""Catalog captions do not become invented scientific scope constraints."""

from copy import deepcopy

import pytest

from scione.matching.metadata import canonical_alignment_pair
from scione.specifications.models import (
    CustomerRequirement,
    RequirementEvidence,
)
from scione.structured.models import AtomicClaim


def pair(context, *, parameter="spacing", test_name="Cross-cut adhesion"):
    claim = AtomicClaim(
        id="source",
        parameter_id=parameter,
        label="Spacing",
        kind="condition",
        operator="=",
        value=2,
        unit="mm",
        raw_text="Use a grid spacing of 2 mm.",
        source_context=context,
        source_pages=[1],
    )
    lab = claim.model_copy(update={"id": "lab", "source_context": None, "source_pages": []})
    source = CustomerRequirement(
        id="req_caption",
        original_test_name=test_name,
        requested_methods=[{"code": "ISO 2409"}],
        evidence=[RequirementEvidence(page=1, quote=claim.raw_text)],
        atomic_claims=[claim],
    )
    return claim, lab, source


def test_exact_selected_owner_caption_is_detached_and_auditable():
    claim, lab, source = pair("Spacing; Cross-cut adhesion, ISO 2409")
    before = deepcopy((claim.model_dump(), lab.model_dump(), source.model_dump()))
    first, second, actions = canonical_alignment_pair(
        claim, lab, source, [lab], owner_test_labels=("Cross-cut adhesion",)
    )
    assert first.source_context is None
    assert second.source_context is None
    assert actions[0]["rule"] == "selected_catalog_owner_caption"
    assert actions[0]["source"] == claim.source_context
    assert (claim.model_dump(), lab.model_dump(), source.model_dump()) == before


@pytest.mark.parametrize(
    "scope",
    [
        "on steel",
        "after thermal cycling",
        "topcoat",
        "arithmetic mean",
        "step 2",
        "configuration QZ-19",
        "Cross-cut adhesoin",
    ],
)
def test_scientific_or_unfamiliar_fragment_is_never_erased(scope):
    context = f"Cross-cut adhesion, ISO 2409; {scope}"
    claim, lab, source = pair(context)
    first, _second, actions = canonical_alignment_pair(
        claim, lab, source, [lab], owner_test_labels=("Cross-cut adhesion",)
    )
    assert first.source_context == context
    assert not any(a["rule"] == "selected_catalog_owner_caption" for a in actions)


def test_model_test_name_with_scientific_suffix_is_not_a_catalog_owner_proof():
    name = "Cross-cut adhesion after thermal cycling"
    claim, lab, source = pair(f"{name}, ISO 2409", test_name=name)
    first, _second, actions = canonical_alignment_pair(
        claim, lab, source, [lab], owner_test_labels=("Cross-cut adhesion",)
    )
    assert first.source_context == claim.source_context
    assert not any(a["rule"] == "selected_catalog_owner_caption" for a in actions)


def test_no_selected_catalog_label_means_no_owner_name_projection():
    claim, lab, source = pair("Cross-cut adhesion, ISO 2409")
    first, _second, actions = canonical_alignment_pair(claim, lab, source, [lab])
    assert first.source_context == claim.source_context
    assert not any(a["rule"] == "selected_catalog_owner_caption" for a in actions)


def test_multiple_requested_methods_keep_method_specific_context():
    claim, lab, source = pair("Cross-cut adhesion, ISO 2409")
    source.requested_methods.append(type(source.requested_methods[0])(code="ASTM D3359"))
    first, _second, actions = canonical_alignment_pair(
        claim, lab, source, [lab], owner_test_labels=("Cross-cut adhesion",)
    )
    assert first.source_context == claim.source_context
    assert not any(a["rule"] == "selected_catalog_owner_caption" for a in actions)


@pytest.mark.parametrize("caption", ["grid spacing", "Spacing", "measurement of spacing"])
def test_neutral_parameter_caption_needs_no_test_title(caption):
    claim, lab, source = pair(caption)
    first, second, actions = canonical_alignment_pair(claim, lab, source, [lab])
    assert first.source_context is None and second.source_context is None
    assert actions[0]["caption_policy"] == "typed_field_phase_or_selected_owner_v2"
    assert claim.source_context == caption


def test_measured_property_caption_is_not_an_extra_lab_condition():
    claim, lab, source = pair("measured gloss", parameter="gloss")
    first, _, _ = canonical_alignment_pair(claim, lab, source, [lab])
    assert first.source_context is None


def test_redundant_owned_stage_is_cleared_without_erasing_the_stage_axis():
    claim, lab, source = pair("During exposure", parameter="duration")
    quote = "During exposure, maintain the setting for 2 h."
    claim = claim.model_copy(update={"raw_text": quote, "stage": "exposure", "unit": "h"})
    lab = lab.model_copy(update={"stage": "exposure", "unit": "h"})
    source.evidence = [RequirementEvidence(page=1, quote=quote)]
    first, second, actions = canonical_alignment_pair(claim, lab, source, [lab])
    assert first.source_context is None and first.stage == second.stage == "exposure"
    assert actions


@pytest.mark.parametrize(
    "caption", ["During curing", "mean gloss", "cold step temperature", "on steel"]
)
def test_unrepresented_stage_statistic_or_material_remains_literal(caption):
    claim, lab, source = pair(caption)
    first, _, _ = canonical_alignment_pair(claim, lab, source, [lab])
    assert first.source_context == caption
