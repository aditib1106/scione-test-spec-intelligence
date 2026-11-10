"""A redundant property caption cannot hide a phase, material or missing output."""

import pytest

from scione.matching.metadata import canonical_alignment_pair
from scione.parameters import contexts_match
from scione.specifications.models import CustomerRequirement
from scione.structured.models import AtomicClaim


@pytest.mark.parametrize(
    "change", [None, "wrong_phase", "missing_property", "extra_scope", "unowned"]
)
def test_only_an_owned_registered_gloss_caption_repeats_the_selected_measurement_property(change):
    first = AtomicClaim(
        id="r",
        parameter_id="measurement_angle",
        label="Angle",
        stage="measurement",
        operator="=",
        value=60,
        unit="°",
        raw_text="Record the 60° specular-gloss reading.",
        source_context="specular-gloss reading",
        source_pages=[1],
    )
    second = first.model_copy(update={"id": "l", "source_context": None, "raw_text": "Lab angle"})
    gloss = AtomicClaim(
        id="gloss",
        parameter_id="gloss",
        label="Gloss",
        kind="criterion",
        stage="initial",
        operator="range",
        minimum=85,
        maximum=95,
        unit="GU",
        raw_text="Initial gloss85-95GU",
        source_pages=[1],
    )
    lab_gloss = gloss.model_copy(update={"id": "lab_gloss", "kind": "reporting"})
    source = CustomerRequirement(
        id="req_gloss",
        original_test_name="Gloss",
        atomic_claims=[first, gloss],
        evidence=[dict(page=1, quote=first.raw_text), dict(page=1, quote=gloss.raw_text)],
    )
    declarations = [second, lab_gloss]
    if change == "wrong_phase":
        first.stage = second.stage = "after_cycle"
    elif change == "missing_property":
        declarations = [second]
    elif change == "extra_scope":
        first.source_context += " under nitrogen"
    elif change == "unowned":
        source.evidence = []
    a, b, actions = canonical_alignment_pair(first, second, source, declarations)
    assert any(
        x.get("rule") == "owned_registered_measurement_property_caption" for x in actions
    ) == (change is None)
    if change is None:
        assert contexts_match(a, b)
