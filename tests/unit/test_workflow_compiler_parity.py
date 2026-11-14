"""Semantic compilation retains established non-capability workflow notes."""

from copy import deepcopy

import pytest
from test_semantic_matching import RecordingProvider, claim, proposal, snapshot

from scione.matching import SemanticMatchingRunner, compile_matching_run
from scione.structured.comparison import compare_requirement


def instruction(identifier, raw, **extra):
    return claim(
        identifier,
        parameter_id=None,
        kind="instruction",
        normalization_state="unresolved",
        operator="text",
        value=None,
        unit=None,
        text_value=raw,
        raw_text=raw,
        **extra,
    )


def compiled(source):
    run = SemanticMatchingRunner(
        RecordingProvider(
            proposal(
                requested_ids=[row.id for row in source.requirements[0].normalized.claims],
                declared_ids=[],
            )
        )
    ).run(source)
    before = deepcopy(run.model_dump(mode="json"))
    result = compile_matching_run(run, {"req_001": "supported"})[0]
    assert run.model_dump(mode="json") == before
    return result


@pytest.mark.parametrize(
    "raw",
    [
        "Retain raw observations for customer review.",
        "Obtain written approval before testing.",
        "Report panel-level observations separately.",
        "Retain photos of the grids for the customer.",
    ],
)
def test_unknown_semantic_links_preserve_only_existing_workflow_informational_policy(raw):
    source = snapshot(requested=[instruction("workflow", raw)], declared=[])
    expected = compare_requirement(source.requirements[0].normalized, [], method_status="supported")
    note = next(check for check in expected.checks if check.id == "workflow")
    assert note.status == "informational"
    result = compiled(source)
    actual = next(check for check in result.checks if check.id == "workflow")
    assert actual == note
    assert actual.declared is None and not actual.capability_claim_ids
    assert "not an automatic capability" in actual.reason
    assert result.unknown_count == 0
    assert result.supported_count == 1  # Method only; never workflow fulfillment.


def test_numeric_instruction_is_not_a_workflow_note():
    numeric = claim(
        "numeric_instruction",
        kind="instruction",
        value=100,
        raw_text="Report panel-level observations at 100 °C.",
    )
    result = compiled(snapshot(requested=[numeric], declared=[]))
    actual = next(check for check in result.checks if check.id == numeric.id)
    assert actual.status == "unknown"
    assert result.status == "needs_information"


def test_full_source_prepass_does_not_hide_a_mixed_operating_clause():
    raw = "Report panel-level observations at 100 °C in nitrogen atmosphere."
    numeric = claim("temperature", value=100, raw_text=raw)
    residual = instruction("residual", raw)
    source = snapshot(requested=[numeric, residual], declared=[])
    result = compiled(source)
    assert all(check.status == "unknown" for check in result.checks if check.kind != "method")
    assert result.unknown_count == 2


@pytest.mark.parametrize(
    "raw",
    [
        "Do not conduct the test in nitrogen.",
        "An argon atmosphere is required during exposure.",
        "The source excludes irradiated specimens.",
    ],
)
def test_scientific_requirements_and_exclusions_remain_unknown(raw):
    result = compiled(snapshot(requested=[instruction("scientific", raw)], declared=[]))
    assert result.checks[-1].status == "unknown"
    assert result.status == "needs_information"


def test_workflow_information_does_not_erase_source_metadata_review():
    source = snapshot(requested=[instruction("workflow", "Retain raw observations.")], declared=[])
    source.requirements[0].normalized.review_issues = ["Source stage is unproved."]
    result = compiled(source)
    assert (
        next(check for check in result.checks if check.id == "workflow").status == "informational"
    )
    metadata = [check for check in result.checks if check.id.endswith("_normalization_review")]
    assert len(metadata) == 1 and metadata[0].status == "unknown"
    assert result.status == "needs_information"
