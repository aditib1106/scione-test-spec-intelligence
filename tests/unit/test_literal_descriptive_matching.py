"""Source-grounded prose is distinct from invalid quantities or parser debris."""

from copy import deepcopy
from decimal import Decimal

import pytest
from test_semantic_matching import RecordingProvider, claim, proposal, snapshot

from scione.matching import SemanticMatchingRunner, compile_matching_run
from scione.matching.literal_text import literal_descriptive_claim
from scione.structured.models import ClaimCheck

SOURCE = "Make a fine scoring grid through the paint film, then apply the prescribed tape removal."


def setup_case(text=SOURCE, **updates):
    requested = claim(
        "req_claim_1", parameter_id=None, operator="text", value=None, unit=None,
        text_value=text, raw_text=text, normalization_state="unresolved", **updates,
        source_pages=[1],
    )
    declared = claim(
        "lab_claim_1", parameter_id="scoring_procedure", operator="text", value=None,
        unit=None, text_value=SOURCE, raw_text="The lab explicitly offers this procedure.",
    )
    data = snapshot(requested=[requested], declared=[declared])
    data.requirements[0].requirement.evidence[0].quote = text
    return data


def compile_case(data, payload=None):
    run = SemanticMatchingRunner(RecordingProvider(payload or proposal())).run(data)
    result = compile_matching_run(run, {"req_001": "supported"})[0]
    return run, result.checks[-1]


def test_complete_located_descriptive_text_can_match_without_registered_attribute():
    data = setup_case()
    original = deepcopy(data.model_dump(mode="json"))
    run, check = compile_case(data)
    assert check.status == "supported"
    assert check.comparison_basis == "literal_descriptive_correspondence"
    assert check.requirement_claim.parameter_id is None
    assert check.requirement_claim.normalization_state == "unresolved"
    assert data.model_dump(mode="json") == original
    assert run.input_snapshot.model_dump(mode="json") == original
    assert ClaimCheck.model_validate_json(check.model_dump_json()) == check


def test_exact_literal_declaration_is_decisive_while_ai_deferral_is_unchanged():
    payload = proposal()
    link = payload["items"][0]["claim_alignments"][0]
    link.update(relation="unknown", capability_claim_ids=[], reason="Unfamiliar attribute.")
    run, check = compile_case(setup_case(), payload)
    assert check.status == "supported"
    assert check.comparison_basis == "deterministic_literal_text"
    assert run.output.items[0].claim_alignments[0].relation == "unknown"


@pytest.mark.parametrize("variant", [
    "numeric", "partial", "wrong_page", "unlocated", "criterion", "no_lab",
    "negation", "material", "stage",
])
def test_literal_path_cannot_repair_quantities_or_drop_scope(variant):
    data = setup_case()
    requested = data.requirements[0].normalized.claims[0]
    source = data.requirements[0].requirement
    lab = data.capabilities[0].claims[0]
    if variant == "numeric":
        requested.operator = "="
        requested.value = Decimal(31)
        requested.unit = "°C"
    elif variant == "partial":
        requested.text_value = "prescribed"
    elif variant == "wrong_page":
        requested.source_pages = [2]
    elif variant == "unlocated":
        source.evidence[0].quote = "A different procedure."
    elif variant == "criterion":
        requested.kind = "criterion"
    elif variant == "no_lab":
        lab.text_value = "A separately declared service."
    elif variant == "negation":
        lab.text_value = "Do not make a scoring grid or remove tape."
    elif variant == "material":
        lab.source_context = "aluminium only"
    else:
        lab.stage = "after_cycle"
    _, check = compile_case(data)
    assert check.status == "unknown"


@pytest.mark.parametrize("text", [
    "Condition the panel at 31 °C.", "Use five panels.",
    "Allow 1+2 hours.", "Apply a load of 3 unknownunits.",
])
def test_quantitative_clauses_do_not_enter_the_literal_descriptive_path(text):
    data = setup_case(text)
    assert literal_descriptive_claim(
        data.requirements[0].normalized.claims[0], data.requirements[0].requirement
    ) is None


@pytest.mark.parametrize("unit,upper,expected", [
    ("flarps", 30, "supported"), ("flarps", 20, "gap"),
    ("flarpz", 30, "unknown"),
])
def test_custom_numeric_attribute_uses_exact_literal_unit_after_alignment(unit, upper, expected):
    requested = claim(
        "req_claim_1", parameter_id="twilight_flux", label="Twilight flux", operator="=",
        value=Decimal(27), unit="flarps", raw_text="Twilight flux 27 flarps.",
        source_pages=[1],
    )
    declared = claim(
        "lab_claim_1", parameter_id="custom_lab_flux", label="Custom lab flux",
        operator="range", value=None, minimum=Decimal(0), maximum=Decimal(upper), unit=unit,
        raw_text=f"Lab flux 0 to {upper} {unit}.",
    )
    data = snapshot(requested=[requested], declared=[declared])
    data.requirements[0].requirement.evidence[0].quote = requested.raw_text
    _, check = compile_case(data)
    assert check.status == expected
