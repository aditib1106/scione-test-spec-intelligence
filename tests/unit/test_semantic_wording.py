"""Explicit semantic wording proposals preserve source facts and hard boundaries."""

from copy import deepcopy
from decimal import Decimal

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from test_semantic_matching import RecordingProvider, claim, proposal, snapshot

from scione.matching import SemanticMatchingRunner, compile_matching_run
from scione.matching.models import ClaimAlignment
from scione.matching.service import matching_output_schema


def wording(left, right):
    return {
        "requested_text": left,
        "declared_text": right,
        "reason": "The two supplied phrases name the same concept.",
    }


def link_payload(**changes):
    payload = proposal()
    payload["items"][0]["claim_alignments"][0].update(changes)
    return payload


def compile_case(source, payload):
    run = SemanticMatchingRunner(RecordingProvider(payload)).run(source)
    checks = compile_matching_run(run, {"req_001": "supported"})[0].checks
    return run, checks[-1]


def test_parameter_correspondence_alone_does_not_imply_category_value_equivalence():
    requested = claim(
        "req_claim_1",
        parameter_id="specimen_material",
        operator="text",
        value=None,
        unit=None,
        text_value="low-carbon steel",
        raw_text="Specimen: low-carbon steel.",
    )
    declared = claim(
        "lab_claim_1",
        parameter_id="specimen_material",
        operator="text",
        value=None,
        unit=None,
        text_value="mild steel",
        raw_text="Material: mild steel.",
    )
    source = snapshot(requested=[requested], declared=[declared])
    untouched = deepcopy(source.model_dump(mode="json"))
    _, prior = compile_case(source, proposal())
    assert prior.status == "unknown"
    run, check = compile_case(
        source, link_payload(value_equivalence=wording("low-carbon steel", "mild steel"))
    )
    assert check.status == "supported"
    assert check.declared == "mild steel"
    assert check.requirement_claim.text_value == "low-carbon steel"
    assert "explicit AI proposal" in check.reason
    assert run.input_snapshot.model_dump(mode="json") == untouched
    assert source.model_dump(mode="json") == untouched
    assert run.output.items[0].claim_alignments[0].value_equivalence is not None


def test_foreign_context_translation_keeps_numeric_comparison_and_original_units():
    requested = claim(
        "req_claim_1",
        parameter_id="tiempo_exposicion",
        operator="=",
        value=Decimal(90),
        unit="min",
        stage="exposure",
        source_context="dentro de la cámara de humedad",
        raw_text="Duración 90 min dentro de la cámara de humedad.",
    )
    declared = claim(
        "lab_claim_1",
        parameter_id="duration",
        operator="range",
        value=None,
        minimum=Decimal(1),
        maximum=Decimal(2),
        unit="h",
        stage="exposure",
        source_context="inside the humidity chamber",
        raw_text="Duration 1 to 2 h inside the humidity chamber.",
    )
    source = snapshot(requested=[requested], declared=[declared])
    _, prior = compile_case(source, proposal())
    assert prior.status == "unknown"
    run, check = compile_case(
        source,
        link_payload(
            context_equivalence=wording(requested.source_context, declared.source_context)
        ),
    )
    assert check.status == "supported"
    assert check.declared == declared.display_value
    assert run.input_snapshot.capabilities[0].claims[0].unit == "h"
    assert (
        run.input_snapshot.capabilities[0].claims[0].source_context == "inside the humidity chamber"
    )
    assert check.requirement_claim.value == 90
    assert not run.validation_issues


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("XQ-a", "XQ-A"),
        ("XQ+7", "XQ-7"),
        ("Polymer-A", "polymer-a"),
        ("steel", "aluminium"),
        ("D65", "D50"),
        ("steel; copper", "mild steel; copper"),
    ],
)
def test_equivalence_cannot_rename_codes_or_distinct_categories_or_sets(left, right):
    parameter = "illuminant" if left == "D65" else "specimen_material"
    requested = claim(
        "req_claim_1",
        parameter_id=parameter,
        operator="text",
        value=None,
        unit=None,
        text_value=left,
    )
    declared = claim(
        "lab_claim_1",
        parameter_id=parameter,
        operator="text",
        value=None,
        unit=None,
        text_value=right,
    )
    run, check = compile_case(
        snapshot(requested=[requested], declared=[declared]),
        link_payload(value_equivalence=wording(left, right)),
    )
    assert check.status == "unknown"
    assert run.proposal.items[0].claim_alignments[0].value_equivalence is not None
    assert run.output.items[0].claim_alignments[0].value_equivalence is None
    assert run.validation_issues


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("water at inlet", "water at outlet"),
        ("test water", "ambient air"),
        ("steel only", "aluminium only"),
        ("at 20 °C", "at 30 °C"),
        ("under nitrogen", "without nitrogen"),
        ("Polymer-A", "polymer-a"),
        ("inside Rig-A", "inside Rig-B"),
    ],
)
def test_context_equivalence_cannot_erase_protected_scope_differences(left, right):
    requested = claim("req_claim_1", source_context=left)
    declared = claim("lab_claim_1", source_context=right)
    run, check = compile_case(
        snapshot(requested=[requested], declared=[declared]),
        link_payload(context_equivalence=wording(left, right)),
    )
    assert check.status == "unknown"
    assert run.validation_issues


@pytest.mark.parametrize(
    "difference", [{"stage": "curing"}, {"sequence": 2}, {"configuration": "other_setup"}]
)
def test_wording_equivalence_cannot_override_stage_step_or_setup(difference):
    requested = claim(
        "req_claim_1", source_context="inside chamber", sequence=1, configuration="setup_a"
    )
    declared = claim(
        "lab_claim_1", source_context="en la cámara", sequence=1, configuration="setup_a"
    ).model_copy(update=difference)
    run, check = compile_case(
        snapshot(requested=[requested], declared=[declared]),
        link_payload(
            context_equivalence=wording(requested.source_context, declared.source_context)
        ),
    )
    assert check.status == "unknown"
    assert run.validation_issues


def test_missing_or_forged_value_evidence_cannot_supply_a_lab_declaration():
    requested = claim(
        "req_claim_1",
        parameter_id="equipment",
        operator="required",
        value=None,
        unit=None,
        text_value="spectrophotometer",
    )
    for text in (None, "uv lamp"):
        declared = claim(
            "lab_claim_1",
            parameter_id="equipment",
            operator="text",
            value=None,
            unit=None,
            text_value=text,
        )
        run, check = compile_case(
            snapshot(requested=[requested], declared=[declared]),
            link_payload(value_equivalence=wording("spectrophotometer", "spectrophotometer")),
        )
        assert check.status == "unknown"
        assert run.validation_issues


def test_missing_context_cannot_be_created_by_an_equivalence_proposal():
    source = snapshot(requested=[claim("req_claim_1", source_context="inside chamber")])
    run, check = compile_case(
        source, link_payload(context_equivalence=wording("inside chamber", "inside chamber"))
    )
    assert check.status == "unknown"
    assert run.validation_issues


def test_numeric_values_are_not_replaced_by_wording_equivalence():
    requested = claim("req_claim_1", text_value="60")
    declared = claim("lab_claim_1", value=Decimal(30), text_value="30")
    run, check = compile_case(
        snapshot(requested=[requested], declared=[declared]),
        link_payload(value_equivalence=wording("60", "30")),
    )
    assert check.status == "unknown"
    assert run.input_snapshot.capabilities[0].claims[0].value == 30


@pytest.mark.parametrize("relation", ["unknown", "ambiguous"])
def test_unresolved_native_schema_and_local_contract_forbid_wording_equivalences(relation):
    payload = link_payload(
        relation=relation,
        capability_claim_ids=[],
        value_equivalence=wording("low-carbon steel", "mild steel"),
    )
    assert list(Draft202012Validator(matching_output_schema()).iter_errors(payload))
    with pytest.raises(ValidationError):
        ClaimAlignment.model_validate(payload["items"][0]["claim_alignments"][0])


def test_equivalence_native_schema_and_local_contract_require_one_lab_reference():
    payload = link_payload(
        capability_claim_ids=["lab_claim_1", "lab_claim_2"],
        context_equivalence=wording("water", "test water"),
    )
    assert list(Draft202012Validator(matching_output_schema()).iter_errors(payload))
    with pytest.raises(ValidationError):
        ClaimAlignment.model_validate(payload["items"][0]["claim_alignments"][0])
