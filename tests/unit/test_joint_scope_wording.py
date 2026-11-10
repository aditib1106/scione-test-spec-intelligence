"""Cross-field scope requires exact recorded evidence and bounded value wording."""

import re
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from test_semantic_matching import RecordingProvider, claim, proposal, snapshot

from scione.matching import SemanticMatchingRunner, compile_matching_run
from scione.matching.models import ClaimAlignment
from scione.matching.semantics import joint_scope_text
from scione.matching.service import matching_output_schema, prepare_matching_request
from scione.specifications.models import RequirementEvidence


def scenario(**changes):
    requested = claim(
        "req_claim_1",
        parameter_id="specimen_material",
        label="Specimen material",
        kind="condition",
        stage="general",
        operator="text",
        value=None,
        unit=None,
        text_value="flat steel witnesses",
        source_context="same primer as the nominated part",
        raw_text="Prepare flat steel witnesses using the same primer as the nominated part.",
        source_pages=[1],
    )
    declared = claim(
        "lab_claim_1",
        parameter_id="specimen_material",
        label="Specimen material",
        kind="condition",
        stage="general",
        operator="text",
        value=None,
        unit=None,
        text_value="flat steel witness panels using the same primer as the nominated part",
        source_context=None,
        raw_text=(
            "The lab prepares flat steel witness panels using the same "
            "primer as the nominated part."
        ),
        source_pages=[],
    )
    requested = requested.model_copy(update=changes.pop("request", {}))
    declared = declared.model_copy(update=changes.pop("lab", {}))
    source = snapshot(requested=[requested], declared=[declared])
    source.requirements[0].requirement.evidence = [
        RequirementEvidence(page=1, quote=requested.raw_text)
    ]
    payload = proposal()
    payload["items"][0]["claim_alignments"][0].update(
        joint_scope_equivalence={
            "requested_text": joint_scope_text(requested) or "unavailable source view",
            "declared_text": joint_scope_text(declared) or "unavailable lab view",
            "reason": (
                "The same recorded qualifier is inside the lab value rather than its context."
            ),
        }
    )
    return source, payload


def run_case(source, payload):
    run = SemanticMatchingRunner(RecordingProvider(payload)).run(source)
    check = compile_matching_run(run, {"req_001": "supported"})[0].checks[-1]
    return run, check


def test_source_owned_scope_inside_lab_value_is_compared_without_changing_artifacts():
    source, payload = scenario()
    before = deepcopy(source.model_dump(mode="json"))
    run, check = run_case(source, payload)
    assert check.status == "supported"
    assert "explicit AI proposal" in check.reason
    assert check.declared == source.capabilities[0].claims[0].display_value
    assert source.model_dump(mode="json") == before
    assert run.input_snapshot.model_dump(mode="json") == before
    assert run.proposal.items[0].claim_alignments[0].joint_scope_equivalence is not None


def test_adjacent_owned_source_context_can_follow_a_narrow_native_quote():
    given, payload = scenario(
        request={"raw_text": "Prepare flat steel witnesses using the"},
    )
    context = given.requirements[0].normalized.claims[0].source_context
    given.requirements[0].requirement.evidence = [
        RequirementEvidence(page=1, quote=f"Prepare flat steel witnesses using the {context}.")
    ]
    before = given.model_dump_json()
    _, check = run_case(given, payload)
    assert check.status == "supported"
    assert given.model_dump_json() == before


@pytest.mark.parametrize(
    "intervening", [" Separately, use ", " except ", ". Another specimen has "]
)
def test_joint_scope_cannot_borrow_a_nonadjacent_qualifier(intervening):
    given, payload = scenario(request={"raw_text": "Prepare flat steel witnesses"})
    context = given.requirements[0].normalized.claims[0].source_context
    given.requirements[0].requirement.evidence = [
        RequirementEvidence(page=1, quote=f"Prepare flat steel witnesses{intervening}{context}.")
    ]
    _, check = run_case(given, payload)
    assert check.status == "unknown"


@pytest.mark.parametrize(
    "request_changes,lab",
    [
        ({"source_context": "a missing primer scope"}, {}),
        (
            {},
            {
                "text_value": (
                    "flat aluminium witness panels using the same primer as the nominated part"
                )
            },
        ),
        (
            {},
            {
                "text_value": (
                    "flat steel witness panels in water using the same primer as the nominated part"
                )
            },
        ),
        (
            {},
            {
                "text_value": (
                    "not flat steel witness panels using the same primer as the nominated part"
                )
            },
        ),
        ({}, {"stage": "after_cycle"}),
        ({}, {"configuration": "another_setup"}),
        ({}, {"sequence": 2}),
        ({"source_pages": [2]}, {}),
        ({"source_pages": []}, {}),
        ({"kind": "criterion"}, {"kind": "reporting"}),
        ({"parameter_id": "unknown_property"}, {"parameter_id": "unknown_property"}),
        (
            {},
            {
                "text_value": (
                    "flat steel witness panels using the same primer as the nominated part only"
                )
            },
        ),
        (
            {"text_value": "steel grade XQ-a"},
            {"text_value": "steel grade XQ-A using the same primer as the nominated part"},
        ),
        (
            {},
            {
                "text_value": (
                    "flat steel witnesses at 30 °C using the same primer as the nominated part"
                )
            },
        ),
    ],
)
def test_scope_proposal_cannot_supply_or_erase_source_constraints(request_changes, lab):
    source, payload = scenario(request=request_changes, lab=lab)
    run, check = run_case(source, payload)
    assert check.status == "unknown"
    assert run.output.items[0].claim_alignments[0].joint_scope_equivalence is None


def test_equal_value_with_lab_only_context_is_not_permission_to_erase_the_restriction():
    source, payload = scenario(
        request={"text_value": "D65", "source_context": None, "parameter_id": "illuminant"},
        lab={
            "text_value": "D65",
            "source_context": "master-panel comparison",
            "parameter_id": "illuminant",
        },
    )
    _, check = run_case(source, payload)
    assert check.status == "unknown"


def test_joint_and_fieldwise_proposals_are_mutually_exclusive_in_native_schema():
    _, payload = scenario()
    link = payload["items"][0]["claim_alignments"][0]
    link["context_equivalence"] = {
        "requested_text": "source",
        "declared_text": "lab",
        "reason": "bad",
    }
    assert not Draft202012Validator(matching_output_schema()).is_valid(payload)
    with pytest.raises(ValidationError, match="cannot be combined"):
        ClaimAlignment.model_validate(link)


def test_paid_wire_exposes_only_derived_bounded_joint_views_and_retains_all_original_fields():
    source, _ = scenario()
    request = prepare_matching_request(source, keyed=True)
    import json

    payload = json.loads(request.user_prompt)
    assert "joint_scope_texts" not in payload
    assert payload["requirements"][0]["normalized"]["claims"][0]["source_context"] == (
        source.requirements[0].normalized.claims[0].source_context
    )


@pytest.mark.parametrize(
    "transport",
    [
        "quote_entities",
        "json_presentation",
        "selected_fields",
        "typographic_quotes",
        "unquoted_delimiters",
    ],
)
def test_joint_transport_accepts_only_lossless_original_field_binding(transport):
    import json

    given, payload = scenario()
    bridge = payload["items"][0]["claim_alignments"][0]["joint_scope_equivalence"]
    for side in ("requested_text", "declared_text"):
        if transport == "quote_entities":
            bridge[side] = bridge[side].replace('"', "&quot;")
        elif transport == "json_presentation":
            bridge[side] = json.dumps(json.loads(bridge[side]), sort_keys=True, indent=2)
        elif transport == "typographic_quotes":
            bridge[side] = re.sub(r'"([^"\\]*)"', lambda m: "«" + m[1] + "»", bridge[side])
        elif transport == "unquoted_delimiters":
            bridge[side] = bridge[side].replace('"', "")
        else:
            original = (
                given.requirements[0].normalized.claims[0]
                if side == "requested_text"
                else given.capabilities[0].claims[0]
            )
            bridge[side] = original.text_value
    run, check = run_case(given, payload)
    assert check.status == "supported"
    assert (
        run.proposal.items[0].claim_alignments[0].joint_scope_equivalence.requested_text
        == bridge["requested_text"]
    )


@pytest.mark.parametrize(
    "corruption", ["extra_key", "duplicate_key", "changed_value", "missing_context"]
)
def test_joint_transport_cannot_repair_missing_changed_or_duplicate_fields(corruption):
    import json

    given, payload = scenario()
    bridge = payload["items"][0]["claim_alignments"][0]["joint_scope_equivalence"]
    obj = json.loads(bridge["declared_text"])
    if corruption == "extra_key":
        obj["availability"] = "yes"
    elif corruption == "changed_value":
        obj["value"] = obj["value"].replace("steel", "aluminium")
    elif corruption == "missing_context":
        del obj["context"]
    if corruption == "duplicate_key":
        bridge["declared_text"] = (
            '{"context":null,"value":"bad","value":' + json.dumps(obj["value"]) + "}"
        )
    else:
        bridge["declared_text"] = json.dumps(obj)
    _, check = run_case(given, payload)
    assert check.status == "unknown"
