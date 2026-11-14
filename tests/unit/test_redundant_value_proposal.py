"""Exactly equal source values do not require an AI wording-equivalence proof."""

from copy import deepcopy

import pytest
from test_semantic_matching import RecordingProvider, claim, snapshot
from test_semantic_wording import link_payload, wording

from scione.matching import SemanticMatchingRunner, compile_matching_run


def source_pair(left="D65", right="D65", **declaration_changes):
    def value(identifier, text, **extra):
        return claim(
            identifier,
            parameter_id="illuminant",
            operator="text",
            value=None,
            unit=None,
            text_value=text,
            raw_text=f"under {text} lighting",
            **extra,
        )

    return snapshot(
        requested=[value("req_claim_1", left)],
        declared=[value("lab_claim_1", right, **declaration_changes)],
    )


def test_recorded_equal_d65_survives_redundant_proposal_quoting_raw_text():
    source = source_pair()
    source_before = deepcopy(source.model_dump(mode="json"))
    payload = link_payload(value_equivalence=wording("under D65 lighting", "D65"))
    payload_before = deepcopy(payload)
    run = SemanticMatchingRunner(RecordingProvider(payload)).run(source)
    link = run.output.items[0].claim_alignments[0]
    assert link.relation == "same_parameter" and link.value_equivalence is None
    assert run.validation_issues[0].code == "unneeded_value_proposal_ignored"
    assert (
        run.proposal.items[0].claim_alignments[0].value_equivalence.requested_text
        == "under D65 lighting"
    )
    assert run.parsed_response == payload_before and run.raw_response
    assert payload == payload_before and source.model_dump(mode="json") == source_before
    result = compile_matching_run(run, {"req_001": "supported"})[0]
    assert result.status == "fully_matched"
    assert result.checks[-1].declared == "D65"


@pytest.mark.parametrize(
    "left,right", [("AX17", "AX21"), ("AX-17", "AX17"), ("XQ-a", "XQ-A"), ("D65", "D50")]
)
def test_different_actual_identifiers_or_categories_are_never_salvaged(left, right):
    source = source_pair(left, right)
    payload = link_payload(value_equivalence=wording(f"under {left} lighting", right))
    run = SemanticMatchingRunner(RecordingProvider(payload)).run(source)
    assert run.output.items[0].claim_alignments[0].relation == "unknown"
    assert not any(
        issue.code == "unneeded_value_proposal_ignored" for issue in run.validation_issues
    )
    assert compile_matching_run(run, {"req_001": "supported"})[0].status == "needs_information"


@pytest.mark.parametrize(
    "difference", [{"stage": "after_aging"}, {"configuration": "Rig B"}, {"sequence": 2}]
)
def test_exact_value_does_not_override_stage_configuration_or_sequence(difference):
    source = source_pair(**difference)
    source.requirements[0].normalized.claims[0].configuration = "Rig A"
    source.requirements[0].normalized.claims[0].sequence = 1
    run = SemanticMatchingRunner(
        RecordingProvider(link_payload(value_equivalence=wording("under D65 lighting", "D65")))
    ).run(source)
    assert run.output.items[0].claim_alignments[0].relation == "unknown"
    assert any(issue.code == "incompatible_claim_context" for issue in run.validation_issues)


def test_exact_value_never_overrides_conflicting_registered_parameters():
    source = source_pair()
    source.capabilities[0].claims[0].parameter_id = "specimen_material"
    source.capabilities[0].claims[0].label = "Specimen material"
    run = SemanticMatchingRunner(
        RecordingProvider(link_payload(value_equivalence=wording("under D65 lighting", "D65")))
    ).run(source)
    assert run.output.items[0].claim_alignments[0].relation == "unknown"
    assert any(issue.code == "incompatible_claim_context" for issue in run.validation_issues)


def test_exact_value_does_not_relax_context_proposal_binding():
    source = source_pair()
    source.requirements[0].normalized.claims[0].source_context = "surface"
    source.capabilities[0].claims[0].source_context = "surface"
    payload = link_payload(
        value_equivalence=wording("under D65 lighting", "D65"),
        context_equivalence=wording("invented source context", "surface"),
    )
    run = SemanticMatchingRunner(RecordingProvider(payload)).run(source)
    assert run.output.items[0].claim_alignments[0].relation == "unknown"


@pytest.mark.parametrize("availability,expected", [("unknown", "unknown"), ("unsupported", "gap")])
def test_exact_value_does_not_make_unavailable_or_missing_lab_support_available(
    availability, expected
):
    source = source_pair(availability=availability)
    run = SemanticMatchingRunner(
        RecordingProvider(link_payload(value_equivalence=wording("under D65 lighting", "D65")))
    ).run(source)
    assert compile_matching_run(run, {"req_001": "supported"})[0].checks[-1].status == expected
