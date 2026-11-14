"""AI source-context proposals retain uncertain obligations and never assert support."""

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from test_semantic_matching import RecordingProvider, claim, proposal, snapshot

from scione.matching import SemanticMatchingRunner, compile_matching_run
from scione.matching.models import ClaimAlignment, SemanticMatchingOutput
from scione.matching.service import source_note_problem, validate_matching_output
from scione.specifications.models import RequirementEvidence


def case(text="Retain raw observations and submit proposed deviations before testing."):
    native = claim("native_temp", raw_text="Water temperature 60 °C", source_pages=[1])
    note = claim(
        "fallback_note",
        parameter_id=None,
        label="Source wording",
        value=None,
        operator="text",
        unit=None,
        text_value=text,
        raw_text=text,
        source_pages=[1],
        normalization_state="unresolved",
    )
    s = snapshot(requested=[native, note])
    row = s.requirements[0]
    row.requirement.atomic_claims = [native.model_copy(deep=True)]
    row.requirement.evidence.append(RequirementEvidence(page=1, quote=text))
    row.normalized.claims = [native, note]
    link = ClaimAlignment(
        requirement_claim_id=note.id,
        capability_claim_ids=[],
        relation="unknown",
        reason="Source administrative wording.",
        context_role="administrative",
    )
    return s, row, native, note, link


def test_source_administrative_note_is_informational_not_capability_support():
    s, row, native, note, link = case()
    before = deepcopy(s.model_dump(mode="json"))
    p = proposal(requested_ids=[native.id, note.id])
    p["items"][0]["claim_alignments"][1] = link.model_dump(mode="json")
    run = SemanticMatchingRunner(RecordingProvider(p)).run(s)
    result = compile_matching_run(run, {row.requirement.id: "supported"})[0]
    check = next(c for c in result.checks if c.id == note.id)
    assert check.status == "informational"
    assert check.comparison_metadata[0]["establishes_lab_support"] is False
    assert not check.capability_claim_ids
    assert s.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "text",
    [
        "Record temperature 60 °C before approval.",
        "Use nitrogen for the method and submit approval.",
        "The fixture QZ19 needs approval.",
        "Approval requires an unknown zorbulation field.",
        "Each panel must be recorded individually for release review.",
    ],
)
def test_scientific_or_unknown_obligations_cannot_be_source_notes(text):
    s, row, _, note, link = case(text)
    assert source_note_problem(note, link, row)
    p = proposal(requested_ids=[c.id for c in row.normalized.claims])
    p["items"][0]["claim_alignments"][1] = link.model_dump(mode="json")
    accepted, issues = validate_matching_output(s, SemanticMatchingOutput.model_validate(p))
    assert accepted.items[0].claim_alignments[1].context_role is None
    assert any(i.code == "source_context_note_rejected" for i in issues)


def test_native_fact_and_unlocated_prose_remain_requirements():
    _, row, native, note, link = case()
    assert source_note_problem(native, link, row)
    row.requirement.evidence = []
    assert source_note_problem(note, link, row)


def test_duplicate_categorical_source_fact_needs_complete_literal_coverage():
    s, row, native, note, link = case("Retain raw observations.")
    native = native.model_copy(
        update={
            "parameter_id": "custom_instruction",
            "value": None,
            "operator": "text",
            "unit": None,
            "kind": "instruction",
            "text_value": note.raw_text,
            "raw_text": note.raw_text,
        }
    )
    row.requirement.atomic_claims = [native]
    row.normalized.claims = [native, note]
    link.context_role = "already_represented"
    link.represented_by_source_ids = [native.id]
    assert source_note_problem(note, link, row) is None
    note = note.model_copy(update={"raw_text": "Retain raw observations with nitrogen."})
    row.requirement.evidence.append(RequirementEvidence(page=1, quote=note.raw_text))
    assert source_note_problem(note, link, row)


def test_strict_wire_schema_supports_notes_while_old_outputs_load_with_defaults():
    s, row, native, note, link = case()
    p = proposal(requested_ids=[native.id, note.id])
    p["items"][0]["claim_alignments"][1] = link.model_dump(mode="json")
    run = SemanticMatchingRunner(RecordingProvider(p)).run(s)
    Draft202012Validator(run.request.json_schema).validate(p)
    old = proposal()
    for row in old["items"]:
        for alignment in row["claim_alignments"]:
            alignment.pop("context_role")
            alignment.pop("represented_by_source_ids")
    assert (
        SemanticMatchingOutput.model_validate(old).items[0].claim_alignments[0].context_role is None
    )


def test_unproved_numeric_criterion_and_condition_proxy_remain_requirements():
    _, row, native, note, link = case("Water temperature 60 °C")
    native = native.model_copy(
        update={
            "kind": "criterion",
            "parameter_id": "water_temperature",
            "label": "Water temperature",
        }
    )
    note = note.model_copy(update={"kind": "criterion"})
    row.requirement.atomic_claims = [native]
    row.normalized.claims = [native, note]
    link.context_role = "already_represented"
    link.represented_by_source_ids = [native.id]
    assert source_note_problem(note, link, row)
    row.normalized.claims[0] = native.model_copy(update={"kind": "condition"})
    assert "condition" in source_note_problem(note, link, row).lower()


def test_duplicate_advisory_references_do_not_reject_other_matching_output():
    s, row, native, note, link = case()
    payload = link.model_dump(mode="json")
    payload["represented_by_source_ids"] = [native.id, native.id]
    advisory = ClaimAlignment.model_validate(payload)
    assert "repeat" in source_note_problem(note, advisory, row)
    p = proposal(requested_ids=[native.id, note.id])
    p["items"][0]["claim_alignments"][1] = payload
    accepted, issues = validate_matching_output(s, SemanticMatchingOutput.model_validate(p))
    assert accepted.items[0].claim_alignments[0].relation == "same_parameter"
    assert accepted.items[0].claim_alignments[1].context_role is None
    assert any(issue.code == "source_context_note_rejected" for issue in issues)


def test_ai_can_explain_paraphrased_duplicate_after_numeric_and_scope_guards():
    text = "A water setting equal to 60 °C is the requested operating point."
    _, row, native, note, link = case(text)
    native = native.model_copy(
        update={"text_value": "Water temperature", "source_context": "water"}
    )
    row.requirement.atomic_claims = [native]
    row.normalized.claims = [native, note]
    link.context_role = "already_represented"
    link.represented_by_source_ids = [native.id]
    assert source_note_problem(note, link, row) is None
    for changed in (
        "A water setting equal to 90 °C is the requested operating point.",
        "A water setting >= 60 °C is the requested operating point.",
        "A water setting equal to 60 °C with nitrogen is the requested operating point.",
    ):
        other = note.model_copy(update={"raw_text": changed, "text_value": changed})
        row.requirement.evidence.append(RequirementEvidence(page=1, quote=changed))
        assert source_note_problem(other, link, row)
