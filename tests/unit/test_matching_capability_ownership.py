"""Incompatible candidate pairs request review without accepting cross-test support."""

import json
from copy import deepcopy

import pytest
from test_semantic_matching import RecordingProvider, claim, proposal, snapshot

from scione.matching import SemanticMatchingRunner, compile_matching_run
from scione.matching.models import SemanticMatchingOutput
from scione.matching.service import MatchingReferenceError, validate_matching_output


def two_rows():
    source = snapshot()
    source.requirements[0].catalog_test_ids.append("other_test")
    second = source.requirements[0].model_copy(deep=True)
    second.requirement.id = "req_002"
    second.normalized.requirement_id = "req_002"
    second.normalized.claims = [claim("req_valid")]
    source.requirements.append(second)
    payload = proposal()
    valid = deepcopy(payload["items"][0])
    valid["requirement_id"] = "req_002"
    valid["claim_alignments"][0]["requirement_claim_id"] = "req_valid"
    payload["items"][0]["canonical_test_id"] = "other_test"
    payload["items"].append(valid)
    return source, payload


def test_cross_owner_candidate_pair_downgrades_only_accepted_copy_and_valid_row_survives():
    source, payload = two_rows()
    source_before = source.model_dump_json()
    payload_before = deepcopy(payload)
    raw = " \n" + json.dumps(payload) + "\n "
    result = SemanticMatchingRunner(RecordingProvider(payload, raw_text=raw)).run(source)

    assert result.status == "completed"
    assert result.raw_response == raw
    assert result.parsed_response == payload_before
    assert result.proposal == SemanticMatchingOutput.model_validate(payload_before)
    assert result.output.items[1] == result.proposal.items[1]
    invalid = result.output.items[0]
    assert invalid.identity_status == "unknown"
    assert invalid.canonical_test_id is None and invalid.capability_id is None
    assert invalid.claim_alignments[0].relation == "unknown"
    assert invalid.claim_alignments[0].capability_claim_ids == []
    assert "another test item" in invalid.reason
    assert [(issue.requirement_id, issue.code) for issue in result.validation_issues] == [
        ("req_001", "incompatible_test_capability_owner"),
    ]
    comparisons = compile_matching_run(result, {"req_001": "supported", "req_002": "supported"})
    assert [row.status for row in comparisons] == ["needs_information", "fully_matched"]
    assert comparisons[0].checks[0].status == "unknown"
    assert payload == payload_before
    assert source.model_dump_json() == source_before


def test_cross_owner_downgrade_clears_every_semantic_bridge_and_source_context_role():
    source, payload = two_rows()
    source_row = source.requirements[0]
    source_row.normalized.claims = [claim(f"req_bad_{i}") for i in range(1, 5)]
    source_row.requirement.atomic_claims = [claim("native_context", source_pages=[1])]
    initial = payload["items"][0]["claim_alignments"][0]
    links = []
    bridge = {"requested_text": "source", "declared_text": "lab", "reason": "proposal"}
    for index in range(1, 5):
        link = {**deepcopy(initial), "requirement_claim_id": f"req_bad_{index}"}
        if index == 1:
            link.update(context_equivalence=bridge, value_equivalence=deepcopy(bridge))
        elif index == 2:
            link["joint_scope_equivalence"] = deepcopy(bridge)
        elif index == 3:
            link.update(scope_relation="equivalent", value_relation="covered")
        else:
            link.update(
                relation="unknown", capability_claim_ids=[], context_role="sample_plan",
                represented_by_source_ids=["native_context"],
            )
        links.append(link)
    payload["items"][0]["claim_alignments"] = links
    machine_proposal = SemanticMatchingOutput.model_validate(payload)
    before = machine_proposal.model_dump_json()

    accepted, issues = validate_matching_output(source, machine_proposal)

    for link in accepted.items[0].claim_alignments:
        assert link.relation == "unknown" and link.capability_claim_ids == []
        assert link.context_equivalence is link.value_equivalence is None
        assert link.joint_scope_equivalence is link.context_role is None
        assert link.scope_relation is link.value_relation is None
        assert link.represented_by_source_ids == []
    assert accepted.items[1] == machine_proposal.items[1]
    assert [issue.code for issue in issues] == ["incompatible_test_capability_owner"]
    assert machine_proposal.model_dump_json() == before
    SemanticMatchingOutput.model_validate_json(accepted.model_dump_json())


@pytest.mark.parametrize("defect", [
    "forged_lab_claim", "other_capability_claim", "missing_claim", "missing_row",
    "outside_test", "outside_capability", "forged_source_context_id",
])
def test_owner_downgrade_cannot_swallow_missing_forged_or_out_of_candidate_references(defect):
    source, payload = two_rows()
    row = payload["items"][0]
    link = row["claim_alignments"][0]
    if defect == "forged_lab_claim":
        link["capability_claim_ids"] = ["not_in_input"]
    elif defect == "other_capability_claim":
        other = source.capabilities[0].model_copy(deep=True)
        other.capability.id = "other_capability"
        other.claims = [claim("claim_of_another_capability")]
        source.capabilities.append(other)
        source.requirements[0].capability_ids.append(other.capability.id)
        link["capability_claim_ids"] = ["claim_of_another_capability"]
    elif defect == "missing_claim":
        row["claim_alignments"] = []
    elif defect == "missing_row":
        payload["items"].pop()
    elif defect == "outside_test":
        row["canonical_test_id"] = "not_a_candidate"
    elif defect == "outside_capability":
        row["capability_id"] = "not_a_candidate"
    else:
        link.update(
            relation="unknown", capability_claim_ids=[], context_role="sample_plan",
            represented_by_source_ids=["not_a_source_claim"],
        )
    machine_proposal = SemanticMatchingOutput.model_validate(payload)
    before = machine_proposal.model_dump_json()
    with pytest.raises(MatchingReferenceError):
        validate_matching_output(source, machine_proposal)
    assert machine_proposal.model_dump_json() == before
